from __future__ import annotations

import copy
import json
import os
import re
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import time

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from pydantic import BaseModel, Field


class BlockType(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    IMAGE_PLACEHOLDER = "image_placeholder"


class StyleTokens(BaseModel):
    font_family: str | None = None
    font_size_pt: float | None = None
    font_weight: Literal["normal", "bold"] | None = None
    color_hex: str | None = None
    background_hex: str | None = None
    align: Literal["left", "center", "right", "justify"] | None = None
    border_visible: bool | None = None


class BoundingBox(BaseModel):
    x_px: float = 0
    y_px: float = 0
    width_px: float = 0
    height_px: float = 0


class DocumentBlock(BaseModel):
    id: str
    type: BlockType
    text: str | None = None
    items: list[str] | None = None
    rows: list[list[str]] | None = None
    image_ref: str | None = None
    bbox: BoundingBox | None = None
    style: StyleTokens = Field(default_factory=StyleTokens)
    lock_tier: int = Field(default=3, ge=1, le=3)


class DocumentLayout(BaseModel):
    title: str
    page_width_px: int = 816
    page_height_px: int = 1056
    margin_px: dict[str, int] = Field(
        default_factory=lambda: {"top": 72, "right": 72, "bottom": 72, "left": 72}
    )
    blocks: list[DocumentBlock]


class AuditReport(BaseModel):
    approved: bool
    missing_text: list[str] = Field(default_factory=list)
    layout_issues: list[str] = Field(default_factory=list)
    patch_instructions: str | None = None


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@lru_cache(maxsize=1)
def _core_client() -> genai.Client:
    return genai.Client(api_key=_require_env("GEMMA_API_KEY_CORE"))


@lru_cache(maxsize=1)
def _audit_client() -> genai.Client:
    return genai.Client(api_key=_require_env("GEMMA_API_KEY_AUDIT"))


def _core_model() -> str:
    return os.environ.get("GEMMA_MODEL", "gemma-4")


def _audit_model() -> str:
    return os.environ.get("GEMMA_AUDIT_MODEL", "gemma-4")


def _vision_model() -> str:
    return os.environ.get("VITEGRID_VISION_MODEL", os.environ.get("GEMMA_MODEL", "gemma-4"))


def _generation_model() -> str:
    return os.environ.get("VITEGRID_GENERATION_MODEL", os.environ.get("GEMMA_MODEL", "gemma-4"))


_DROP_KEYS = {"additionalProperties", "title", "$defs", "$schema"}


def _clean_schema(schema: type[BaseModel]) -> dict[str, Any]:
    raw = schema.model_json_schema()
    defs = raw.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                ref_name = node["$ref"].rsplit("/", 1)[-1]
                target = copy.deepcopy(defs.get(ref_name, {}))
                return resolve(target)
            if "anyOf" in node:
                variants = node["anyOf"]
                non_null = [v for v in variants if v.get("type") != "null"]
                has_null = len(non_null) != len(variants)
                if len(non_null) == 1:
                    merged = {k: v for k, v in node.items() if k != "anyOf"}
                    merged.update(resolve(non_null[0]))
                    if has_null:
                        merged["nullable"] = True
                    return merged
                node["anyOf"] = [resolve(v) for v in variants]
            cleaned: dict[str, Any] = {}
            for key, value in node.items():
                if key in _DROP_KEYS:
                    continue
                if key == "properties" and isinstance(value, dict):
                    cleaned[key] = {pname: resolve(pschema) for pname, pschema in value.items()}
                else:
                    cleaned[key] = resolve(value)
            return cleaned
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(raw)


def _json_config(schema: type[BaseModel]) -> genai_types.GenerateContentConfig:
    return genai_types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=_clean_schema(schema),
        temperature=0.2,
    )


def _call_with_retry(client: genai.Client, **kwargs: Any) -> Any:
    last_exc: Exception | None = None
    for attempt in range(4):
        try:
            return client.models.generate_content(**kwargs)
        except genai_errors.ServerError as exc:
            last_exc = exc
            time.sleep(1.5 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def _parse_response(response: Any, schema: type[BaseModel]) -> BaseModel:
    text = (getattr(response, "text", None) or "").strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(text)
    return schema.model_validate(obj)


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUM_LIST_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_TABLE_LINE_RE = re.compile(r"^\s*\|")


def _next_id(counter: list[int]) -> str:
    block_id = f"block-{counter[0]}"
    counter[0] += 1
    return block_id


def _collect_paragraph(buffer: list[str], blocks: list[DocumentBlock], counter: list[int]) -> None:
    text = " ".join(line.strip() for line in buffer if line.strip())
    if not text:
        return
    blocks.append(
        DocumentBlock(
            id=_next_id(counter),
            type=BlockType.PARAGRAPH,
            text=text,
            style=StyleTokens(font_size_pt=11.0, align="left"),
        )
    )


def _markdown_to_blocks(markdown: str) -> list[DocumentBlock]:
    blocks: list[DocumentBlock] = []
    counter = [0]
    paragraph: list[str] = []
    pending_list: list[str] = []
    pending_table: list[list[str]] = []

    def flush_list() -> None:
        if pending_list:
            blocks.append(
                DocumentBlock(
                    id=_next_id(counter),
                    type=BlockType.LIST,
                    items=list(pending_list),
                    style=StyleTokens(font_size_pt=11.0, align="left"),
                )
            )
            pending_list.clear()

    def flush_table() -> None:
        if pending_table:
            blocks.append(
                DocumentBlock(
                    id=_next_id(counter),
                    type=BlockType.TABLE,
                    rows=[list(row) for row in pending_table],
                    style=StyleTokens(font_size_pt=10.0, border_visible=True),
                )
            )
            pending_table.clear()

    def flush_paragraph() -> None:
        _collect_paragraph(paragraph, blocks, counter)
        paragraph.clear()

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            flush_paragraph()
            flush_list()
            flush_table()
            continue
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            flush_paragraph()
            flush_list()
            flush_table()
            level = len(heading_match.group(1))
            text = heading_match.group(2).strip()
            font_pt = max(12.0, 22.0 - (level - 1) * 2.0)
            blocks.append(
                DocumentBlock(
                    id=_next_id(counter),
                    type=BlockType.HEADING,
                    text=text,
                    style=StyleTokens(
                        font_size_pt=font_pt,
                        font_weight="bold",
                        align="left",
                    ),
                )
            )
            continue
        list_match = _LIST_RE.match(line) or _NUM_LIST_RE.match(line)
        if list_match:
            flush_paragraph()
            flush_table()
            pending_list.append(list_match.group(1).strip())
            continue
        if _TABLE_LINE_RE.match(line):
            flush_paragraph()
            flush_list()
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if all(set(cell) <= set("-: ") for cell in cells):
                continue
            pending_table.append(cells)
            continue
        flush_list()
        flush_table()
        paragraph.append(line)

    flush_paragraph()
    flush_list()
    flush_table()
    return blocks


def agent1_structural_parse(markdown: str, tables: list[dict[str, Any]]) -> DocumentLayout:
    blocks = _markdown_to_blocks(markdown)
    md_table_count = sum(1 for b in blocks if b.type == BlockType.TABLE)
    counter = [len(blocks)]
    for table in tables[md_table_count:]:
        rows = table.get("cells", [])
        if not rows:
            continue
        blocks.append(
            DocumentBlock(
                id=_next_id(counter),
                type=BlockType.TABLE,
                rows=rows,
                style=StyleTokens(font_size_pt=10.0, border_visible=True),
            )
        )

    first_heading = next((b.text for b in blocks if b.type == BlockType.HEADING and b.text), None)
    title = first_heading or "Imported Document"
    return DocumentLayout(title=title, blocks=blocks)


_AGENT2_PROMPT = """You are Agent 2: the Visual Style Token Evaluator for Vitegrid.

Ignore textual content. Examine the page image and extract design tokens:
font family hints, body font size in points, common heading weight, primary
text color hex, background color hex, dominant alignment, table border
visibility, and page margin estimates in pixels.
Return a JSON StyleTokens-shaped object for each detected region."""


class StyleProfile(BaseModel):
    page_margin_px: dict[str, int] = Field(
        default_factory=lambda: {"top": 72, "right": 72, "bottom": 72, "left": 72}
    )
    heading: StyleTokens = Field(default_factory=StyleTokens)
    body: StyleTokens = Field(default_factory=StyleTokens)
    table: StyleTokens = Field(default_factory=StyleTokens)


def agent2_style_evaluate(image_path: str | Path) -> StyleProfile:
    path = Path(image_path)
    image_bytes = path.read_bytes()
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    response = _call_with_retry(
        _core_client(),
        model=_vision_model(),
        contents=[
            _AGENT2_PROMPT,
            genai_types.Part.from_bytes(data=image_bytes, mime_type=mime),
        ],
        config=_json_config(StyleProfile),
    )
    return _parse_response(response, StyleProfile)  # type: ignore[return-value]


_AGENT3_PROMPT = """You are Agent 3: the Content Generation & Schema Writer for Vitegrid.

Produce a complete, richly-structured DocumentLayout for the user's goal. The
output is a one-page document on US Letter at 96 dpi (816 x 1056 px) with 72px
margins. Always favor STRUCTURE over a single wall of text.

Rules:
- Title: a concrete, specific title (never "Untitled" or "Document").
- Open with one heading block, then alternate between paragraphs, lists, and
  tables as the content warrants. Aim for 5-10 blocks total, not 1-3.
- Use heading for section titles, paragraph for prose, list for enumerable
  points, table for structured data (e.g. skills matrix, dates), and
  image_placeholder where a visual asset would naturally go (leave image_ref
  null so the user can attach later).
- Every block needs an id of the form block-0, block-1, ... in reading order.
- Style: set font_family to a sensible serif/sans for the genre, set
  font_size_pt (10-12 for body, 16-22 for headings), pick align, leave
  background_hex null unless the design genuinely calls for color.
- Bbox: omit (null) unless you have a strong reason. A deterministic layout
  pass will assign positions.
- lock_tier defaults to 3.

Example for the goal "Resume for a software engineering new-grad":
- block-0 heading "Jane Doe" centered 22pt
- block-1 paragraph contact line centered 10pt
- block-2 heading "Experience" 14pt
- block-3 paragraph role summary
- block-4 list of accomplishment bullets
- block-5 heading "Education" 14pt
- block-6 paragraph degree details
- block-7 heading "Skills" 14pt
- block-8 table 2 columns x 3 rows of skill categories
"""


def agent3_generate_from_prompt(user_goal: str, patch_directive: str | None = None) -> DocumentLayout:
    parts: list[str] = [_AGENT3_PROMPT, f"User goal:\n{user_goal}"]
    if patch_directive:
        parts.append(f"Auditor patch instructions to address:\n{patch_directive}")
    config = _json_config(DocumentLayout)
    config.temperature = 0.5
    response = _call_with_retry(
        _core_client(),
        model=_generation_model(),
        contents=parts,
        config=config,
    )
    return _parse_response(response, DocumentLayout)  # type: ignore[return-value]


_AGENT4_PROMPT = """You are Agent 4: the independent QA Auditor for Vitegrid.

Inputs: {"source": ..., "layout": DocumentLayout}.
The source is EITHER {"user_goal": "..."} for a freshly generated layout OR
{"markdown": "...", "tables": [...]} for an imported document.

Apply each check below. Set approved=false ONLY if a numbered check fails.

CHECK 1 (text fidelity, imports only):
  If source has markdown, every non-empty content line from the markdown must
  appear inside some block's text/items/rows. Whitespace and markdown syntax
  (# bullets pipes) are not content. Missing real content -> fail, list
  missing_text entries.

CHECK 2 (block count, generated only):
  If source has user_goal, layout.blocks must have at least 3 blocks. Fewer
  -> fail with patch_instructions: "Expand to at least 5 blocks with headings".

CHECK 3 (id ordering):
  Block ids must be strings; uniqueness required. Out-of-order or duplicate
  ids -> add to layout_issues.

CHECK 4 (bbox sanity):
  For each block whose bbox is NOT null: x_px>=0, y_px>=0, width_px>0,
  height_px>0, and (x_px+width_px) <= page_width_px,
  (y_px+height_px) <= page_height_px. Null bbox is allowed and not a failure.

CHECK 5 (heading presence on imports):
  If source markdown contains lines starting with '#', layout must have at
  least one heading block.

If no check fails, approved=true with empty lists and null patch_instructions.
Otherwise approved=false; patch_instructions is ONE concrete sentence Agent 3
can act on (e.g. "Add a heading before block-2 and expand bullet list to 5
items")."""


def agent4_audit(source_payload: dict[str, Any], layout: DocumentLayout) -> AuditReport:
    payload = json.dumps({"source": source_payload, "layout": layout.model_dump()})
    response = _call_with_retry(
        _audit_client(),
        model=_audit_model(),
        contents=[_AGENT4_PROMPT, payload],
        config=_json_config(AuditReport),
    )
    return _parse_response(response, AuditReport)  # type: ignore[return-value]


def _estimate_block_height(block: DocumentBlock, content_width: float) -> float:
    chars_per_line = max(40, int(content_width / 7))
    if block.type == BlockType.HEADING:
        font_pt = block.style.font_size_pt or 18.0
        return font_pt * 2.0 + 16
    if block.type == BlockType.PARAGRAPH:
        text = block.text or ""
        line_height = (block.style.font_size_pt or 11.0) * 1.5
        lines = max(1, -(-len(text) // chars_per_line))
        return lines * line_height + 8
    if block.type == BlockType.LIST:
        items = block.items or []
        line_height = (block.style.font_size_pt or 11.0) * 1.5
        return max(1, len(items)) * line_height + 8
    if block.type == BlockType.TABLE:
        rows = len(block.rows or [])
        return max(1, rows) * 28 + 8
    if block.type == BlockType.IMAGE_PLACEHOLDER:
        return 220
    return 32


def _normalize_margins(raw: dict[str, int] | None) -> dict[str, int]:
    raw = raw or {}
    aliases = {
        "top": ("top", "t", "marginTop", "margin_top"),
        "right": ("right", "r", "marginRight", "margin_right"),
        "bottom": ("bottom", "b", "marginBottom", "margin_bottom"),
        "left": ("left", "l", "marginLeft", "margin_left"),
    }
    out: dict[str, int] = {}
    for canonical, names in aliases.items():
        value = next((raw[n] for n in names if n in raw and raw[n] is not None), 72)
        try:
            out[canonical] = int(value)
        except (TypeError, ValueError):
            out[canonical] = 72
    return out


def auto_layout(layout: DocumentLayout) -> DocumentLayout:
    layout.margin_px = _normalize_margins(layout.margin_px)
    content_x = layout.margin_px["left"]
    content_y = layout.margin_px["top"]
    content_width = max(
        1, layout.page_width_px - layout.margin_px["left"] - layout.margin_px["right"]
    )
    cursor_y = float(content_y)
    for block in layout.blocks:
        if block.bbox is not None and block.bbox.width_px > 0 and block.bbox.height_px > 0:
            cursor_y = max(cursor_y, block.bbox.y_px + block.bbox.height_px + 8)
            continue
        height = _estimate_block_height(block, content_width)
        block.bbox = BoundingBox(
            x_px=float(content_x),
            y_px=cursor_y,
            width_px=float(content_width),
            height_px=height,
        )
        cursor_y += height + 8
    return layout


def generate_from_prompt(user_goal: str, max_retries: int = 1) -> tuple[DocumentLayout, AuditReport]:
    layout = auto_layout(agent3_generate_from_prompt(user_goal))
    report = agent4_audit({"user_goal": user_goal}, layout)
    attempts = 0
    while not report.approved and attempts < max_retries:
        layout = auto_layout(
            agent3_generate_from_prompt(user_goal, patch_directive=report.patch_instructions)
        )
        report = agent4_audit({"user_goal": user_goal}, layout)
        attempts += 1
    return layout, report


def import_from_parsed(
    markdown: str,
    tables: list[dict[str, Any]],
    template_image_path: str | Path | None = None,
) -> tuple[DocumentLayout, AuditReport]:
    layout = agent1_structural_parse(markdown, tables)
    if template_image_path is not None:
        style = agent2_style_evaluate(template_image_path)
        layout.margin_px = style.page_margin_px
        for block in layout.blocks:
            if block.type == BlockType.HEADING:
                block.style = style.heading
            elif block.type == BlockType.TABLE:
                block.style = style.table
            else:
                block.style = style.body
    layout = auto_layout(layout)
    report = agent4_audit({"markdown": markdown, "tables": tables}, layout)
    return layout, report


class _BlockProposal(BaseModel):
    block_type: BlockType
    span_indices: list[int] = Field(default_factory=list)
    list_items: list[list[int]] = Field(default_factory=list)
    table_rows: list[list[list[int]]] = Field(default_factory=list)
    align: Literal["left", "center", "right", "justify"] = "left"


class _PageGrouping(BaseModel):
    blocks: list[_BlockProposal]


_AGENT5_PROMPT = """You are Agent 5: the Semantic Document Labeler for Vitegrid.

The input you receive for each page is:
1. A PNG image of the page (visual reference).
2. A list of pre-extracted text spans. Each span has an index, exact text,
   exact bbox (x0, y0, x1, y1) in PDF points, exact font, exact size, exact
   color, exact bold/italic flags. THIS DATA IS GROUND TRUTH. Do not retype it.

Your ONLY job: group the spans into semantic blocks and label each block's
type. NEVER emit the text content yourself; refer to spans by their index.

For each block on the page emit a _BlockProposal:
- block_type: one of "heading" | "paragraph" | "list" | "table" | "image_placeholder"
- span_indices: ordered list of span indices for heading or paragraph blocks
- list_items: ONE inner list of span indices per bullet/numbered item (used for list blocks)
- table_rows: rows -> columns -> spans (used for table blocks)
- align: "left" | "center" | "right" | "justify" inferred from x-coordinates

Rules:
- Use the page image to decide visual emphasis (what is a heading vs a paragraph).
- Use spans' size_pt and bold flags as strong signals (larger + bold = heading).
- Group spans that visually belong together (same line, same paragraph, same column).
- A list has multiple short items at similar x-coordinates with bullets or numbers
  in nearby spans; group each item's content spans into one inner list.
- A table is a grid; rows are aligned by y, columns are aligned by x.
- Output blocks in TOP-TO-BOTTOM reading order.
- EVERY non-noise span index from the input MUST appear in some block's span_indices,
  list_items, or table_rows. Do not drop content.
- Do not invent indices that were not in the input.
"""


def _assemble_text_from_spans(span_indices: list[int], spans: list[Any]) -> str:
    parts = [spans[i] for i in span_indices if 0 <= i < len(spans)]
    if not parts:
        return ""
    parts.sort(key=lambda s: (round(s.bbox[1] / 4), s.bbox[0]))
    out_lines: list[list[str]] = [[]]
    last_y: float | None = None
    line_height = max((p.size_pt for p in parts), default=12.0) * 1.2
    for span in parts:
        cy = (span.bbox[1] + span.bbox[3]) / 2
        if last_y is not None and abs(cy - last_y) > line_height * 0.6:
            out_lines.append([])
        out_lines[-1].append(span.text)
        last_y = cy
    return "\n".join(" ".join(line).strip() for line in out_lines).strip()


def _dominant_style(span_indices: list[int], spans: list[Any]) -> StyleTokens:
    used = [spans[i] for i in span_indices if 0 <= i < len(spans)]
    if not used:
        return StyleTokens()
    sizes = [s.size_pt for s in used if s.size_pt]
    fonts = [s.font for s in used if s.font]
    colors = [s.color_hex for s in used if s.color_hex]
    weights = ["bold" if s.bold else "normal" for s in used]
    return StyleTokens(
        font_family=max(set(fonts), key=fonts.count) if fonts else None,
        font_size_pt=round(sum(sizes) / len(sizes), 1) if sizes else None,
        font_weight="bold" if weights.count("bold") > len(weights) / 2 else "normal",
        color_hex=max(set(colors), key=colors.count) if colors else None,
    )


def _proposal_to_block(prop: _BlockProposal, spans: list[Any], block_id: str) -> DocumentBlock:
    if prop.block_type == BlockType.LIST:
        items = [_assemble_text_from_spans(ids, spans) for ids in prop.list_items]
        items = [it for it in items if it]
        all_indices = [i for sub in prop.list_items for i in sub]
        style = _dominant_style(all_indices, spans)
        style.align = prop.align
        return DocumentBlock(
            id=block_id,
            type=BlockType.LIST,
            items=items,
            style=style,
        )
    if prop.block_type == BlockType.TABLE:
        rows = [
            [_assemble_text_from_spans(cell, spans) for cell in row]
            for row in prop.table_rows
        ]
        all_indices = [i for row in prop.table_rows for cell in row for i in cell]
        style = _dominant_style(all_indices, spans)
        style.align = prop.align
        style.border_visible = True
        return DocumentBlock(
            id=block_id,
            type=BlockType.TABLE,
            rows=rows,
            style=style,
        )
    if prop.block_type == BlockType.IMAGE_PLACEHOLDER:
        return DocumentBlock(id=block_id, type=BlockType.IMAGE_PLACEHOLDER, style=StyleTokens())
    text = _assemble_text_from_spans(prop.span_indices, spans)
    style = _dominant_style(prop.span_indices, spans)
    style.align = prop.align
    return DocumentBlock(id=block_id, type=prop.block_type, text=text, style=style)


def agent5_label_page(page_image: bytes, spans: list[Any], page_index: int) -> _PageGrouping:
    span_dump = [
        {
            "index": i,
            "text": s.text,
            "bbox": [round(x, 1) for x in s.bbox],
            "size_pt": round(s.size_pt, 1),
            "font": s.font,
            "color": s.color_hex,
            "bold": s.bold,
            "italic": s.italic,
        }
        for i, s in enumerate(spans)
    ]
    parts: list[Any] = [
        _AGENT5_PROMPT,
        f"Page {page_index + 1} spans (ground truth, do not retype):\n"
        + json.dumps(span_dump, ensure_ascii=False),
        genai_types.Part.from_bytes(data=page_image, mime_type="image/png"),
    ]
    config = _json_config(_PageGrouping)
    config.temperature = 0.0
    response = _call_with_retry(
        _core_client(),
        model=_vision_model(),
        contents=parts,
        config=config,
    )
    return _parse_response(response, _PageGrouping)  # type: ignore[return-value]


def import_from_pdf_extraction(extraction: Any) -> tuple[DocumentLayout, AuditReport]:
    blocks: list[DocumentBlock] = []
    counter = 0
    page_width_pt = extraction.pages[0].page_width_pt if extraction.pages else 612.0
    page_height_pt = extraction.pages[0].page_height_pt if extraction.pages else 792.0

    if extraction.is_scanned:
        layout = agent5_vision_only_scanned([p.image_bytes for p in extraction.pages])
    else:
        for page in extraction.pages:
            grouping = agent5_label_page(page.image_bytes, page.spans, page.page_index)
            covered: set[int] = set()
            for prop in grouping.blocks:
                block_id = f"block-{counter}"
                counter += 1
                block = _proposal_to_block(prop, page.spans, block_id)
                blocks.append(block)
                covered.update(prop.span_indices)
                for item in prop.list_items:
                    covered.update(item)
                for row in prop.table_rows:
                    for cell in row:
                        covered.update(cell)
            missing = [i for i in range(len(page.spans)) if i not in covered and page.spans[i].text.strip()]
            for idx in missing:
                span = page.spans[idx]
                blocks.append(
                    DocumentBlock(
                        id=f"block-{counter}",
                        type=BlockType.PARAGRAPH,
                        text=span.text,
                        style=StyleTokens(
                            font_family=span.font or None,
                            font_size_pt=span.size_pt or None,
                            font_weight="bold" if span.bold else "normal",
                            color_hex=span.color_hex,
                        ),
                    )
                )
                counter += 1
        first_heading = next(
            (b.text for b in blocks if b.type == BlockType.HEADING and b.text), None
        )
        title = first_heading or "Imported Document"
        scale = 816.0 / page_width_pt if page_width_pt else 1.0
        page_w = round(page_width_pt * scale)
        page_h = round(page_height_pt * scale)
        layout = DocumentLayout(
            title=title,
            page_width_px=page_w,
            page_height_px=page_h,
            blocks=blocks,
        )

    layout = auto_layout(layout)
    report = agent4_audit({"pdf_pages": len(extraction.pages)}, layout)
    return layout, report


_AGENT5_OCR_PROMPT = """You are Agent 5 (fallback OCR path) for a SCANNED PDF.

No embedded text was found, so you must read the page image as a human would.
Produce a complete DocumentLayout JSON object containing every visible text
element. Include text, bbox in PDF points, font family guess, size in points,
color hex, weight, and alignment. Be exhaustive.
"""


def agent5_vision_only_scanned(page_images: list[bytes]) -> DocumentLayout:
    parts: list[Any] = [_AGENT5_OCR_PROMPT]
    for image_bytes in page_images:
        parts.append(genai_types.Part.from_bytes(data=image_bytes, mime_type="image/png"))
    config = _json_config(DocumentLayout)
    config.temperature = 0.0
    response = _call_with_retry(
        _core_client(),
        model=_vision_model(),
        contents=parts,
        config=config,
    )
    return _parse_response(response, DocumentLayout)  # type: ignore[return-value]


class _BlockReview(BaseModel):
    index: int
    correct_type: BlockType
    confidence: float = 0.5


class _BlockReviewResponse(BaseModel):
    reviews: list[_BlockReview] = Field(default_factory=list)


_AGENT_REVIEW_PROMPT = """You are a Block Type Reviewer for Vitegrid.

You receive a page image and a list of candidate blocks already extracted
from the PDF. Each candidate has an index, a proposed block_type, a bbox,
and a short text preview.

For each block, decide whether the proposed type is correct. If not,
return the correct type. Valid types:
- heading: large or visually emphasized title/section label
- paragraph: prose text
- list: bulleted/numbered enumeration
- table: grid of rows and columns
- image_placeholder: figure or photo region

Trust the bbox and preview. Look at the page image to judge visual emphasis
and structure. Confidence should be 0-1.

Only return reviews for blocks where you DISAGREE with the proposed type
(confidence > 0.6). Skip blocks you agree with."""


def agent_review_block_types(
    page_image: bytes,
    page_index: int,
    candidates: list[Any],
) -> list[_BlockReview]:
    if not candidates:
        return []
    candidate_dump = [
        {
            "index": i,
            "proposed_type": c.block_type,
            "bbox": [round(x, 1) for x in c.bbox],
            "preview": (c.text or " | ".join(c.items or []) or str(c.rows or ""))[:120],
            "is_bold": c.bold,
            "size_pt": c.size_pt,
        }
        for i, c in enumerate(candidates)
    ]
    parts: list[Any] = [
        _AGENT_REVIEW_PROMPT,
        f"Page {page_index + 1} candidates:\n" + json.dumps(candidate_dump, ensure_ascii=False),
        genai_types.Part.from_bytes(data=page_image, mime_type="image/png"),
    ]
    config = _json_config(_BlockReviewResponse)
    config.temperature = 0.0
    try:
        response = _call_with_retry(
            _core_client(),
            model=_vision_model(),
            contents=parts,
            config=config,
        )
        parsed = _parse_response(response, _BlockReviewResponse)
        return parsed.reviews if isinstance(parsed, _BlockReviewResponse) else []
    except Exception:
        return []


def ensemble_pdf_import(extraction: Any) -> tuple[DocumentLayout, AuditReport]:
    classified = list(_run_classifier(extraction))
    if not classified:
        return DocumentLayout(title="Empty Document", blocks=[]), AuditReport(approved=True)

    by_page: dict[int, list[int]] = {}
    for idx, c in enumerate(classified):
        by_page.setdefault(c.page_index, []).append(idx)

    for page in extraction.pages:
        indices = by_page.get(page.page_index, [])
        if not indices:
            continue
        page_candidates = [classified[i] for i in indices]
        reviews = agent_review_block_types(page.image_bytes, page.page_index, page_candidates)
        for review in reviews:
            if 0 <= review.index < len(indices) and review.confidence >= 0.6:
                target_idx = indices[review.index]
                original = classified[target_idx]
                if original.block_type != review.correct_type.value:
                    classified[target_idx] = _coerce_block_type(original, review.correct_type.value)

    page_w = extraction.pages[0].page_width_pt if extraction.pages else 612.0
    page_h = extraction.pages[0].page_height_pt if extraction.pages else 792.0
    return import_from_classified_blocks(classified, page_w, page_h, len(extraction.pages))


def _run_classifier(extraction: Any) -> list[Any]:
    from parser import classify_pdf_layout

    return classify_pdf_layout(extraction)


def _coerce_block_type(original: Any, new_type: str) -> Any:
    import dataclasses

    if not dataclasses.is_dataclass(original):
        return original
    text_join = original.text or " ".join(original.items or []) or " ".join(
        cell for row in (original.rows or []) for cell in row
    )
    if new_type == "list":
        items = original.items if original.items else [text_join] if text_join else []
        return dataclasses.replace(
            original, block_type="list", text=None, items=items, rows=None
        )
    if new_type == "table":
        rows = original.rows if original.rows else [[text_join]] if text_join else []
        return dataclasses.replace(
            original, block_type="table", text=None, items=None, rows=rows
        )
    if new_type in ("heading", "paragraph"):
        return dataclasses.replace(
            original,
            block_type=new_type,
            text=text_join if text_join else original.text,
            items=None,
            rows=None,
        )
    if new_type == "image_placeholder":
        return dataclasses.replace(
            original, block_type="image_placeholder", text=None, items=None, rows=None
        )
    return original


def import_from_classified_blocks(
    classified: list[Any],
    page_width_pt: float,
    page_height_pt: float,
    page_count: int,
) -> tuple[DocumentLayout, AuditReport]:
    blocks: list[DocumentBlock] = []
    scale = 816.0 / page_width_pt if page_width_pt else 1.0
    page_w = max(816, round(page_width_pt * scale))
    page_h = max(1056, round(page_height_pt * scale))
    for i, c in enumerate(classified):
        bx0, by0, bx1, by1 = c.bbox
        bbox = BoundingBox(
            x_px=round(bx0 * scale, 1),
            y_px=round(by0 * scale, 1),
            width_px=round((bx1 - bx0) * scale, 1),
            height_px=round((by1 - by0) * scale, 1),
        )
        style = StyleTokens(
            font_family=c.font,
            font_size_pt=c.size_pt,
            font_weight="bold" if c.bold else "normal",
            color_hex=c.color_hex,
            align=c.align,
            border_visible=True if c.block_type == "table" else None,
        )
        blocks.append(
            DocumentBlock(
                id=f"block-{i}",
                type=BlockType(c.block_type),
                text=c.text,
                items=c.items,
                rows=c.rows,
                bbox=bbox,
                style=style,
            )
        )
    first_heading = next(
        (b.text for b in blocks if b.type == BlockType.HEADING and b.text), None
    )
    title = first_heading or "Imported Document"
    layout = DocumentLayout(
        title=title,
        page_width_px=page_w,
        page_height_px=page_h,
        blocks=blocks,
    )
    layout = auto_layout(layout)
    report = agent4_audit({"pdf_pages": page_count, "classifier": "heuristic"}, layout)
    return layout, report


def import_from_docx_blocks(docx_blocks: list[Any]) -> tuple[DocumentLayout, AuditReport]:
    blocks: list[DocumentBlock] = []
    for i, b in enumerate(docx_blocks):
        style = StyleTokens(
            font_family=b.font,
            font_size_pt=b.size_pt,
            font_weight="bold" if b.bold else "normal",
            color_hex=b.color_hex,
            align=b.align,
            border_visible=True if b.type == "table" else None,
        )
        block_type = BlockType(b.type)
        blocks.append(
            DocumentBlock(
                id=f"block-{i}",
                type=block_type,
                text=b.text,
                items=b.items,
                rows=b.rows,
                style=style,
            )
        )
    first_heading = next(
        (b.text for b in blocks if b.type == BlockType.HEADING and b.text), None
    )
    title = first_heading or "Imported Document"
    layout = DocumentLayout(title=title, blocks=blocks)
    layout = auto_layout(layout)
    report = agent4_audit({"docx_blocks": len(blocks)}, layout)
    return layout, report


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatResponse(BaseModel):
    assistant_message: str
    updated_layout: DocumentLayout | None = None


_AGENT6_PROMPT = """You are Agent 6: the Conversational Editor for Vitegrid.

You receive the current DocumentLayout, the prior chat turns, and a new user
message. Decide whether the user is asking for an edit (change content,
restructure, restyle, add/remove blocks) or just chatting/asking a question.

If an edit is requested:
  - Produce a complete updated_layout that applies the change.
  - Preserve all blocks the user did NOT ask to change. Keep their ids, text,
    styles, and bboxes intact unless the request specifically touches them.
  - For new blocks, assign new ids that don't collide (block-N where N is the
    next available number).
  - In assistant_message, briefly confirm what you changed (1-2 sentences).

If no edit is requested:
  - Set updated_layout to null.
  - Answer the user's question in assistant_message.

Always respond as JSON matching the ChatResponse schema.
"""


def agent6_chat(layout: DocumentLayout, history: list[ChatTurn], user_message: str) -> ChatResponse:
    payload = json.dumps(
        {
            "current_layout": layout.model_dump(),
            "history": [t.model_dump() for t in history],
            "user_message": user_message,
        }
    )
    config = _json_config(ChatResponse)
    config.temperature = 0.3
    response = _call_with_retry(
        _core_client(),
        model=_generation_model(),
        contents=[_AGENT6_PROMPT, payload],
        config=config,
    )
    result = _parse_response(response, ChatResponse)  # type: ignore[assignment]
    if isinstance(result, ChatResponse) and result.updated_layout is not None:
        result.updated_layout = auto_layout(result.updated_layout)
    return result  # type: ignore[return-value]
