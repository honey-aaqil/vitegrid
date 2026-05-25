from __future__ import annotations

import copy
import json
import os
import re
import types
import uuid
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin

import time

import pymupdf

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from pydantic import BaseModel, Field, model_validator


def _annotation_allows_none(annotation: Any) -> bool:
    """True if the annotation's type union explicitly includes None."""
    if annotation is None or annotation is type(None):
        return True
    origin = get_origin(annotation)
    if origin is Union or origin is getattr(types, "UnionType", Union):
        return type(None) in get_args(annotation)
    return False


def _coerce_nones_to_defaults(cls: type[BaseModel], data: Any) -> Any:
    """Drop None values for non-Optional fields so Pydantic uses field defaults.

    Lets legacy callsites pass ``font_family=None`` to express "no opinion"
    without violating the blueprint's mandatory-with-defaults schema.
    """
    if not isinstance(data, dict):
        return data
    cleaned = dict(data)
    for name, field in cls.model_fields.items():
        if name in cleaned and cleaned[name] is None and not _annotation_allows_none(
            field.annotation
        ):
            cleaned.pop(name)
    return cleaned


class BlockType(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    IMAGE_PLACEHOLDER = "image_placeholder"


class ListFormat(str, Enum):
    DECIMAL = "decimal"
    BULLET = "bullet"
    LOWER_LETTER = "lowerLetter"
    UPPER_ROMAN = "upperRoman"


def _default_cell_padding_dxa() -> dict[str, int]:
    return {"top": 120, "bottom": 120, "left": 180, "right": 180}


def _default_margin_px() -> dict[str, float]:
    return {"top": 72.0, "right": 72.0, "bottom": 72.0, "left": 72.0}


class SpacingTokens(BaseModel):
    before_dxa: int = Field(default=0, description="Paragraph spacing before in twips/dxa.")
    after_dxa: int = Field(default=0, description="Paragraph spacing after in twips/dxa.")
    line_spacing_dxa: int = Field(
        default=240, description="Exact line spacing in twips (240 twips = 12pt)."
    )
    line_rule: Literal["auto", "exact", "atLeast"] = Field(
        default="auto", description="Line spacing rule: auto, exact, atLeast."
    )

    @model_validator(mode="before")
    @classmethod
    def _allow_none_in_strict_fields(cls, data: Any) -> Any:
        return _coerce_nones_to_defaults(cls, data)


class StyleTokens(BaseModel):
    font_family: str = Field(default="Arial", description="Primary mapped font family name.")
    font_size_pt: float = Field(default=11.0, description="Absolute font size in points.")
    font_weight: Literal["normal", "bold"] = Field(
        default="normal", description="Font weight: normal or bold."
    )
    color_hex: str = Field(default="000000", description="RGB hexadecimal text color.")
    background_hex: str | None = Field(
        default="FFFFFF", description="Shading RGB hexadecimal fill or None for transparent."
    )
    align: Literal["left", "center", "right", "justify"] = Field(
        default="left", description="Alignment: left, center, right, justify."
    )
    border_visible: bool = Field(default=True, description="Whether structural borders are displayed.")
    cell_padding_dxa: dict[str, int] = Field(
        default_factory=_default_cell_padding_dxa,
        description="Explicit cell margins in twips/dxa: keys top/bottom/left/right.",
    )
    list_format: ListFormat = Field(
        default=ListFormat.BULLET, description="Applied format of list bullet/marker."
    )
    list_level: int = Field(default=0, description="Hierarchical indentation level of list items.")

    @model_validator(mode="before")
    @classmethod
    def _allow_none_in_strict_fields(cls, data: Any) -> Any:
        return _coerce_nones_to_defaults(cls, data)


class BoundingBox(BaseModel):
    x_px: float = Field(default=0.0, description="Web canvas coordinate x in pixels.")
    y_px: float = Field(default=0.0, description="Web canvas coordinate y in pixels.")
    width_px: float = Field(default=0.0, description="Web canvas width in pixels.")
    height_px: float = Field(default=0.0, description="Web canvas height in pixels.")


class DocumentBlock(BaseModel):
    id: str = Field(..., description="Unique alphanumeric structural block identifier.")
    type: BlockType = Field(..., description="Block structural type.")
    text: str | None = Field(default=None, description="Raw text for heading/paragraph elements.")
    items: list[str] | None = Field(default=None, description="Ordered list values for list blocks.")
    rows: list[list[str]] | None = Field(default=None, description="2D table cells.")
    image_ref: str | None = Field(default=None, description="Resource locator for graphic assets.")
    bbox: BoundingBox | None = Field(default=None, description="Canvas bounding box coordinates.")
    style: StyleTokens = Field(default_factory=StyleTokens)
    spacing: SpacingTokens = Field(default_factory=SpacingTokens)
    lock_tier: int = Field(default=1, ge=1, le=3, description="Editing restriction tier.")


class DocumentLayout(BaseModel):
    title: str = Field(..., description="Internal reference document title.")
    page_width_px: float = Field(
        default=816.0, description="Web canvas width in pixels (816px = 8.5in at 96dpi)."
    )
    page_height_px: float = Field(
        default=1056.0, description="Web canvas height in pixels (1056px = 11in at 96dpi)."
    )
    margin_px: dict[str, float] = Field(
        default_factory=_default_margin_px,
        description="Logical page margin values in web pixels.",
    )
    blocks: list[DocumentBlock] = Field(default_factory=list, description="Ordered DOM nodes.")


class AuditReport(BaseModel):
    approved: bool = Field(..., description="Strict visual similarity verification result.")
    missing_text: list[str] = Field(default_factory=list, description="Omitted text run data.")
    layout_issues: list[str] = Field(
        default_factory=list, description="Flagged metric deviations in spatial alignment."
    )
    patch_instructions: str | None = Field(
        default=None, description="Declarative instructions to resolve layout drift."
    )


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


# Fallback when no model env var is set. Must be a real, currently-deployed
# Google AI model so a missing env var doesn't 404 in production. The
# `gemma-4` value previously hard-coded here was never a real public model.
_DEFAULT_MODEL = "gemini-2.5-flash"


def _core_model() -> str:
    return os.environ.get("GEMMA_MODEL", _DEFAULT_MODEL)


def _audit_model() -> str:
    return os.environ.get("GEMMA_AUDIT_MODEL", _DEFAULT_MODEL)


def _vision_model() -> str:
    return os.environ.get(
        "VITEGRID_VISION_MODEL", os.environ.get("GEMMA_MODEL", _DEFAULT_MODEL)
    )


def _generation_model() -> str:
    return os.environ.get(
        "VITEGRID_GENERATION_MODEL", os.environ.get("GEMMA_MODEL", _DEFAULT_MODEL)
    )


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


_AGENT2_PROMPT = """
You are a deterministic optical character and style analyzer inside a high-fidelity document reproduction engine.
Analyze the provided document image and extract styling parameters with absolute mathematical accuracy.

CRITICAL PROCESSING RULES:
1. Parse font metrics based on character geometries:
   - Identify font families by matching glyph properties to known system fonts.
   - Categorize weights by computing the WeightRat = CapH / WStem. Assign "bold" if WeightRat <= 7.5.
2. Measure vertical spatial gaps between structural line baselines. Compute exact leading and line spacing ratios.
3. Compute global margins by locating the extreme boundary coordinates of text elements.
4. If interior grid boundaries exist in tables, set border_visible to true.

Ensure the returned output conforms exactly to the provided JSON schema. No conversational wrappers, no markdown formatting block ticks.

Output Format Schema:
{
  "font_family": "Arial",
  "font_size_pt": 11.0,
  "font_weight": "normal",
  "color_hex": "000000",
  "align": "left",
  "line_spacing_dxa": 240,
  "spacing_before_dxa": 120,
  "spacing_after_dxa": 120,
  "border_visible": true,
  "cell_padding_dxa": {"top": 120, "bottom": 120, "left": 180, "right": 180}
}
"""


class StyleProbe(BaseModel):
    """Flat StyleTokens + SpacingTokens object emitted by Agent 2 per the blueprint."""

    font_family: str = "Arial"
    font_size_pt: float = 11.0
    font_weight: Literal["normal", "bold"] = "normal"
    color_hex: str = "000000"
    background_hex: str | None = "FFFFFF"
    align: Literal["left", "center", "right", "justify"] = "left"
    border_visible: bool = True
    cell_padding_dxa: dict[str, int] = Field(default_factory=_default_cell_padding_dxa)
    line_spacing_dxa: int = 240
    spacing_before_dxa: int = 0
    spacing_after_dxa: int = 0
    page_margin_px: dict[str, float] = Field(default_factory=_default_margin_px)

    def to_style(self) -> StyleTokens:
        return StyleTokens(
            font_family=self.font_family,
            font_size_pt=self.font_size_pt,
            font_weight=self.font_weight,
            color_hex=self.color_hex,
            background_hex=self.background_hex,
            align=self.align,
            border_visible=self.border_visible,
            cell_padding_dxa=dict(self.cell_padding_dxa),
        )

    def to_spacing(self) -> SpacingTokens:
        return SpacingTokens(
            before_dxa=self.spacing_before_dxa,
            after_dxa=self.spacing_after_dxa,
            line_spacing_dxa=self.line_spacing_dxa,
            line_rule="exact" if self.line_spacing_dxa > 0 else "auto",
        )


def agent2_style_evaluate(image_path: str | Path) -> StyleProbe:
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
        config=_json_config(StyleProbe),
    )
    return _parse_response(response, StyleProbe)  # type: ignore[return-value]


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
  height_px>0, and (x_px+width_px) <= page_width_px.

  *** DO NOT FLAG vertical overflow. ***
  Blocks whose y_px or (y_px + height_px) exceed page_height_px are NOT
  bugs. The exported .docx is flow-paginated by Microsoft Word, so a
  document with more than one page of content legitimately has many
  blocks past page_height_px. Treat page_height_px as a hint for page-1
  geometry only; never compare any block's y_px against it. Do not emit
  layout_issues like "extends past bottom", "exceeds page_height_px",
  or anything that compares y_px to page_height_px. Multi-page is normal.

  Null bbox is allowed and not a failure.

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


def _parse_hex(color: str | None) -> tuple[int, int, int] | None:
    if not color:
        return None
    s = color.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return None
    try:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        return None


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(c: int) -> float:
        v = c / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def wcag_contrast(fg_hex: str | None, bg_hex: str | None) -> float | None:
    """WCAG 2.1 contrast ratio. Returns None if either color is unparseable."""
    fg = _parse_hex(fg_hex)
    bg = _parse_hex(bg_hex)
    if fg is None or bg is None:
        return None
    lf, lb = _relative_luminance(fg), _relative_luminance(bg)
    brightest, darkest = max(lf, lb), min(lf, lb)
    return (brightest + 0.05) / (darkest + 0.05)


def _bbox_overlap_metrics(
    a: BoundingBox, b: BoundingBox
) -> tuple[float, float, float]:
    """Return (iou, containment_a_in_b, containment_b_in_a)."""
    ax0, ay0, ax1, ay1 = a.x_px, a.y_px, a.x_px + a.width_px, a.y_px + a.height_px
    bx0, by0, bx1, by1 = b.x_px, b.y_px, b.x_px + b.width_px, b.y_px + b.height_px
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0, 0.0, 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = max(1e-6, (ax1 - ax0) * (ay1 - ay0))
    area_b = max(1e-6, (bx1 - bx0) * (by1 - by0))
    union = area_a + area_b - inter
    return inter / union, inter / area_a, inter / area_b


def _block_text_runs(block: DocumentBlock) -> list[str]:
    parts: list[str] = []
    if block.text:
        parts.append(block.text)
    if block.items:
        parts.extend(block.items)
    if block.rows:
        for row in block.rows:
            parts.extend(row)
    return parts


def audit_layout_programmatic(
    layout: DocumentLayout,
    source_text_runs: list[str] | None = None,
    *,
    iou_threshold: float = 0.15,
    containment_threshold: float = 0.90,
    min_contrast: float = 3.0,
    min_font_pt: float = 4.5,
    max_font_pt: float = 72.0,
) -> AuditReport:
    """Deterministic, non-LLM audit. Complements `agent4_audit`.

    Checks:
      1. Source text containment (only when `source_text_runs` is provided).
      2. Pairwise block-bbox collision (IoU above threshold and not nested).
      3. WCAG contrast between style.color_hex and style.background_hex when both set.
      4. Font size within rendering bounds (default 4.5pt..72pt).
      5. Bbox positive width/height and inside page bounds.
    """
    missing: list[str] = []
    issues: list[str] = []

    if source_text_runs:
        compiled = " ".join(part for block in layout.blocks for part in _block_text_runs(block))
        normalized = " ".join(compiled.split())
        for run in source_text_runs:
            needle = " ".join(run.split())
            if needle and needle not in normalized:
                missing.append(run)

    blocks = layout.blocks
    for i, b1 in enumerate(blocks):
        if b1.bbox is None or b1.bbox.width_px <= 0 or b1.bbox.height_px <= 0:
            if b1.bbox is not None:
                issues.append(f"block[{b1.id}] has non-positive bbox dimensions")
        else:
            if b1.bbox.x_px < 0 or b1.bbox.y_px < 0:
                issues.append(f"block[{b1.id}] bbox starts off-page")
            if b1.bbox.x_px + b1.bbox.width_px > layout.page_width_px + 0.5:
                issues.append(f"block[{b1.id}] extends past right edge")
            # No vertical-edge check: OpenXML flow-paginates the rendered .docx
            # automatically, so a layout with more than one page of content
            # legitimately has blocks with y_px > page_height_px. Horizontal
            # overflow (above) IS a real bug because page width is fixed.

        if b1.style.font_size_pt is not None and (
            b1.style.font_size_pt < min_font_pt or b1.style.font_size_pt > max_font_pt
        ):
            issues.append(
                f"block[{b1.id}] font {b1.style.font_size_pt}pt outside [{min_font_pt},{max_font_pt}]"
            )

        contrast = wcag_contrast(b1.style.color_hex, b1.style.background_hex)
        if contrast is not None and contrast < min_contrast:
            issues.append(
                f"block[{b1.id}] contrast {contrast:.2f}:1 below WCAG-AA threshold {min_contrast}"
            )

        if b1.bbox is None:
            continue
        for b2 in blocks[i + 1 :]:
            if b2.bbox is None:
                continue
            iou, c_ab, c_ba = _bbox_overlap_metrics(b1.bbox, b2.bbox)
            if iou < iou_threshold:
                continue
            if c_ab >= containment_threshold or c_ba >= containment_threshold:
                # one is nested inside the other — acceptable
                continue
            issues.append(
                f"blocks[{b1.id}] and [{b2.id}] overlap (IoU={iou:.2f}) without nesting"
            )

    approved = not missing and not issues
    return AuditReport(
        approved=approved,
        missing_text=missing,
        layout_issues=issues,
        patch_instructions=(
            None
            if approved
            else (
                f"Programmatic audit: {len(missing)} missing text runs, "
                f"{len(issues)} structural issues. Shift overlapping blocks, "
                "tighten contrast, restore omitted text."
            )
        ),
    )


# Patterns that flag vertical-overflow false positives. The exported .docx is
# flow-paginated by Word, so blocks with y_px > page_height_px are legitimate
# multi-page content, not bugs. The LLM auditor occasionally hallucinates this
# check even when the prompt forbids it, so we strip the messages here too.
_BOTTOM_EDGE_FALSE_POSITIVE_PATTERNS = (
    "extends past bottom",
    "past bottom edge",
    "exceeds page_height",
    "exceeds page height",
    "y_px + height_px",
    "below page_height",
    "below page height",
    "outside page bounds (bottom",
)


def _is_bottom_edge_false_positive(issue: str) -> bool:
    lower = issue.lower()
    return any(p in lower for p in _BOTTOM_EDGE_FALSE_POSITIVE_PATTERNS)


def _strip_bottom_edge_false_positives(report: AuditReport) -> AuditReport:
    """Drop bottom-edge / page-height issues from any audit report.

    Vertical overflow is normal for multi-page imports. If filtering leaves
    no issues and no missing text, the report is marked approved.
    """
    kept_issues = [i for i in report.layout_issues if not _is_bottom_edge_false_positive(i)]
    if len(kept_issues) == len(report.layout_issues):
        return report
    approved = not kept_issues and not report.missing_text
    return AuditReport(
        approved=approved,
        missing_text=list(report.missing_text),
        layout_issues=kept_issues,
        patch_instructions=None if approved else report.patch_instructions,
    )


def _merge_reports(*reports: AuditReport) -> AuditReport:
    if not reports:
        return AuditReport(approved=True)
    approved = all(r.approved for r in reports)
    seen_missing: dict[str, None] = {}
    seen_issues: dict[str, None] = {}
    for r in reports:
        for m in r.missing_text:
            seen_missing.setdefault(m, None)
        for i in r.layout_issues:
            seen_issues.setdefault(i, None)
    patch = next((r.patch_instructions for r in reports if r.patch_instructions), None)
    return AuditReport(
        approved=approved,
        missing_text=list(seen_missing),
        layout_issues=list(seen_issues),
        patch_instructions=patch,
    )


def _markdown_text_runs(markdown: str) -> list[str]:
    runs: list[str] = []
    for line in markdown.splitlines():
        stripped = line.strip().lstrip("#").lstrip("-*+").lstrip()
        if not stripped or set(stripped) <= set("|-: "):
            continue
        if stripped.startswith("|") or "|" in line and set(line.strip()) <= set("|-: "):
            continue
        runs.append(stripped)
    return runs


def _combined_audit(
    source_payload: dict[str, Any],
    layout: DocumentLayout,
    source_text_runs: list[str] | None = None,
) -> AuditReport:
    llm = agent4_audit(source_payload, layout)
    prog = audit_layout_programmatic(layout, source_text_runs)
    merged = _merge_reports(llm, prog)
    # Defense in depth: the LLM auditor occasionally invents the off-page-Y
    # check even when the prompt forbids it. Strip those false positives.
    return _strip_bottom_edge_false_positives(merged)


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
    report = _combined_audit({"user_goal": user_goal}, layout)
    attempts = 0
    while not report.approved and attempts < max_retries:
        layout = auto_layout(
            agent3_generate_from_prompt(user_goal, patch_directive=report.patch_instructions)
        )
        report = _combined_audit({"user_goal": user_goal}, layout)
        attempts += 1
    return layout, report


def import_from_parsed(
    markdown: str,
    tables: list[dict[str, Any]],
    template_image_path: str | Path | None = None,
) -> tuple[DocumentLayout, AuditReport]:
    layout = agent1_structural_parse(markdown, tables)
    if template_image_path is not None:
        probe = agent2_style_evaluate(template_image_path)
        layout.margin_px = dict(probe.page_margin_px)
        base_style = probe.to_style()
        base_spacing = probe.to_spacing()
        for block in layout.blocks:
            # The blueprint's flat StyleProbe is a global baseline. Per-block
            # structural traits (heading size, table border) overlay on top.
            new_style = base_style.model_copy()
            if block.type == BlockType.HEADING:
                # Preserve markdown-derived heading size; force bold weight.
                if block.style.font_size_pt:
                    new_style.font_size_pt = block.style.font_size_pt
                new_style.font_weight = "bold"
            elif block.type == BlockType.TABLE:
                new_style.border_visible = True
            block.style = new_style
            block.spacing = base_spacing.model_copy()
    layout = auto_layout(layout)
    report = _combined_audit(
        {"markdown": markdown, "tables": tables},
        layout,
        source_text_runs=_markdown_text_runs(markdown),
    )
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
- STRICT TABLE TOPOLOGY RULE: A table is exclusively for genuine data grids
  containing explicit, visible dividing lines or formal, repetitive data
  headers. DO NOT use "table" for multi-column layout text, key-value pairs,
  or resume skill sections. If text is simply aligned in invisible columns
  without gridlines, you MUST classify it as a "list" or a "paragraph".
- Output blocks in TOP-TO-BOTTOM reading order.
- EVERY non-noise span index from the input MUST appear in some block's span_indices,
  list_items, or table_rows. Do not drop content.
- Do not invent indices that were not in the input.
"""


def _assemble_text_from_spans(span_indices: list[int], spans: list[Any]) -> str:
    """Assemble text spans into continuous strings via proximity-aware gap
    detection so PDF tokenization artifacts (font-subset boundaries, TJ
    operator splits, kerning adjustments) don't fracture words.

    For two consecutive spans S1, S2 on the same baseline, define the
    horizontal gap as `x_gap = S2.x0 - S1.x1`. If `x_gap < font_size * 0.25`,
    treat as intra-word continuation and concatenate without inserting a
    space. Negative gaps (overlap due to kerning) are also intra-word.
    Otherwise insert a single space at the boundary.
    """
    parts = [spans[i] for i in span_indices if 0 <= i < len(spans)]
    if not parts:
        return ""
    # Primary sort: quantized Y baseline (4-pt buckets). Secondary: exact X.
    parts.sort(key=lambda s: (round(s.bbox[1] / 4), s.bbox[0]))
    line_height = max((p.size_pt for p in parts), default=12.0) * 1.2

    out_lines: list[list[str]] = [[]]
    last_y: float | None = None
    last_x_end: float | None = None

    for span in parts:
        cy = (span.bbox[1] + span.bbox[3]) / 2
        new_line = last_y is not None and abs(cy - last_y) > line_height * 0.6
        if new_line:
            out_lines.append([])
            last_x_end = None

        font_size = span.size_pt if span.size_pt else 12.0
        text = span.text

        if not out_lines[-1] or last_x_end is None:
            out_lines[-1].append(text)
        else:
            x_gap = span.bbox[0] - last_x_end
            intra_word_threshold = font_size * 0.25
            # Tight (or slightly overlapping due to kerning) gaps mean the
            # spans are fragments of the same word. Hard cap the negative
            # overlap at -font_size to reject totally unrelated overlapping
            # spans.
            if -font_size < x_gap < intra_word_threshold:
                out_lines[-1][-1] = out_lines[-1][-1] + text
            else:
                out_lines[-1].append(text)

        last_y = cy
        last_x_end = span.bbox[2]

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
    report = _combined_audit({"pdf_pages": len(extraction.pages)}, layout)
    return layout, report


_AGENT_IMAGE_PIPELINE_PROMPT = """
You are a deterministic image-to-document analyzer inside a high-fidelity
reproduction engine. Convert the document image(s) into a complete editable
DocumentLayout JSON object.

THREE-STEP MANDATE — execute every step in order and emit results for ALL
elements you observe. No element may be skipped.

STEP 1 — SPATIAL ANCHORING
Identify EVERY structural element visible in the image, including:
  * All text runs (paragraphs, headings, list items, table cells, captions,
    headers/footers, sidebar text, page numbers).
  * Hidden / structurally implied elements: column gutters, table grid
    lines, decorative separators, header/footer rules, alignment guides.
  * Background fills and border rectangles behind text.
  * Image regions: emit as `image_placeholder` blocks with their bbox.
For each element, emit a precise bbox in WEB PIXELS at 96 DPI:
  bbox = { "x_px": <float>, "y_px": <float>,
           "width_px": <float>, "height_px": <float> }
Reading order: top to bottom; within shared rows, left to right.
Do NOT invent text that is not in the image. Do NOT omit visible text.

CRITICAL TOPOLOGICAL CONSTRAINT: Do NOT group multi-column layouts, resume
skill lists, or key-value structures into `table` blocks unless explicit,
visible gridlines physically intersect them. Such structures must be mapped
to `list` or `paragraph` blocks. True tables MUST possess visible structural
borders.

STEP 2 — OPTICAL PARSING (per element)
For each anchored element, extract its exact visual formatting:
  * font_family: closest standard system font from {Arial, Calibri,
    Helvetica, Times New Roman, Georgia, Verdana, Cambria, Garamond,
    Courier New}.
  * font_size_pt: from glyph cap-height, one decimal place
    (1pt = 1/72in; assume the image is rendered at 96 DPI).
  * font_weight via PANOSE WeightRat = CapH / WStem:
        WeightRat <= 7.5  -->  "bold"
        WeightRat >  7.5  -->  "normal"
  * color_hex: hex of the dominant text color, six digits, no "#".
  * background_hex: hex of the fill BEHIND this element; use `null`
    when the background is pure white (#FFFFFF) or absent.
  * align: "left" | "center" | "right" | "justify"  — inferred from
    x-coordinates of consecutive runs.
  * border_visible: true iff visible grid lines exist between cells.
  * cell_padding_dxa (TABLE elements only): the gap between cell text
    and cell borders, in twentieths of a point  =  (pixel gap) * 15.
For each element, observe its paragraph rhythm:
  * line_spacing_dxa = (baseline-to-baseline pixels) * 15
  * before_dxa, after_dxa = (pixel gap between paragraphs) * 15
  * line_rule = "exact" for typeset prose with a hard line grid,
    "auto" otherwise.

STEP 3 — STYLE MAPPING (per block)
Each block in `blocks[]` carries its OWN StyleTokens and SpacingTokens.
Do NOT collapse multiple blocks to a single shared style. Heading
blocks emit their heading metrics; body blocks emit body metrics; table
cells emit table metrics. List blocks set list_format from
{bullet, decimal, lowerLetter, upperRoman} based on the observed marker.

PAGE GEOMETRY
Set page_width_px and page_height_px to match the document's apparent
proportions at 96 DPI. Default for US Letter portrait is 816 x 1056.
margin_px is the tight bounding rectangle of all text content, in pixels.

OUTPUT FORMAT
Return ONE DocumentLayout JSON object. No markdown fences. No commentary.
Every block has: id ("block-0", "block-1", ... in reading order), type,
bbox, style, spacing, plus text/items/rows/image_ref depending on type.
Every numeric field is a number, not a string. background_hex may be null;
everything else must be present.
"""


def agent5_vision_only_scanned(page_images: list[bytes]) -> DocumentLayout:
    """Multi-page vision-only path for scanned PDFs. Uses the same
    three-step pipeline as :func:`import_from_image`.
    """
    parts: list[Any] = [_AGENT_IMAGE_PIPELINE_PROMPT]
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


_IMAGE_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def import_from_image(image_path: str | Path) -> tuple[DocumentLayout, AuditReport]:
    """Convert a single document image (screenshot / scan) into a complete
    DocumentLayout via the three-step vision pipeline.

    Step 1 anchors every visible/hidden element with bbox coordinates.
    Step 2 extracts per-element formatting (font, weight, color, spacing).
    Step 3 produces a layout where every block carries its OWN style and
    spacing tokens; nothing is collapsed to a global default.

    The returned layout is gated by the combined LLM + programmatic audit
    (overlap detection, contrast, font bounds, bbox sanity).
    """
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {path}")
    suffix = path.suffix.lower()
    mime = _IMAGE_MIME_BY_SUFFIX.get(suffix, "image/png")
    image_bytes = path.read_bytes()

    config = _json_config(DocumentLayout)
    config.temperature = 0.0
    response = _call_with_retry(
        _core_client(),
        model=_vision_model(),
        contents=[
            _AGENT_IMAGE_PIPELINE_PROMPT,
            genai_types.Part.from_bytes(data=image_bytes, mime_type=mime),
        ],
        config=config,
    )
    layout = _parse_response(response, DocumentLayout)
    if not isinstance(layout, DocumentLayout):
        raise RuntimeError("Vision agent did not return a DocumentLayout")

    # Backfill positions for any block the vision agent emitted without bbox.
    # When the agent followed the prompt every block already has one, so
    # `auto_layout` becomes a no-op.
    layout = auto_layout(layout)

    report = _combined_audit({"image_source": path.name}, layout)
    return layout, report


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
- paragraph: prose text (including multi-column layout text without gridlines)
- list: bulleted/numbered enumeration (including aligned key-value resume pairs)
- table: STRICTLY grids of rows and columns enclosed by visible structural lines.
  Do NOT override a paragraph or list back to table merely because text shows
  column-style alignment. Tables require visible borders or formal data headers.
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
    report = _combined_audit({"pdf_pages": page_count, "classifier": "heuristic"}, layout)
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
    report = _combined_audit({"docx_blocks": len(blocks)}, layout)
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


# ---------------------------------------------------------------------------
# Critic-Refiner Agent System (Visual Regression Closed-Loop)
# ---------------------------------------------------------------------------

class BlockStylePatch(BaseModel):
    block_id: str = Field(description="The exact identifier of the target block being modified (e.g., 'block-0').")
    font_size_change_pt: float | None = Field(default=None, description="Delta to apply to the font point size parameter.")
    align_patch: Literal["left", "center", "right", "justify"] | None = Field(default=None, description="Correct block text justifications.")
    font_weight_patch: Literal["normal", "bold"] | None = Field(default=None, description="Toggle font weights to adjust text density.")
    margin_top_shift_px: int | None = Field(default=None, description="Adjust block vertical offset to fix layout drift.")


class LayoutRefinementPatches(BaseModel):
    diagnostics: str = Field(description="A concise summary identifying visual alignment or text break discrepancies.")
    patches: list[BlockStylePatch] = Field(default_factory=list, description="An array of style updates to correct layout errors.")


_REFINER_PROMPT = """You are Agent 5: the Senior Typography Specialist for Vitegrid.

You receive:
1. A ground-truth reference profile image of the target layout.
2. A current web preview canvas screenshot of the active schema metrics.
3. A visual diff overlay image where typographical anomalies are painted in bright RED.
4. The underlying DocumentLayout structural JSON schema.

Examine column boundaries, font sizes, line wrapping points, and alignment deviations.
Produce a structured LayoutRefinementPatches object adjusting only element style tokens.
Do not rewrite content words, drop block containers, or change node tracking indexes."""


def agent_refine_layout_schema(
    gt_bytes: bytes,
    cand_bytes: bytes,
    diff_bytes: bytes,
    current_layout: DocumentLayout,
) -> LayoutRefinementPatches:
    """
    Submits layout screenshot assets alongside the current block JSON
    to the vision model to compute highly accurate style fixes.
    """
    contents = [
        _REFINER_PROMPT,
        f"Active Model Layout State Matrix:\n{current_layout.model_dump_json()}",
        genai_types.Part.from_bytes(data=gt_bytes, mime_type="image/png"),
        genai_types.Part.from_bytes(data=cand_bytes, mime_type="image/png"),
        genai_types.Part.from_bytes(data=diff_bytes, mime_type="image/png"),
    ]
    response = _call_with_retry(
        _core_client(),
        model=_vision_model(),
        contents=contents,
        config=_json_config(LayoutRefinementPatches),
    )
    return _parse_response(response, LayoutRefinementPatches)  # type: ignore


# ---------------------------------------------------------------------------
# Closed-Loop Optimization Controller
# ---------------------------------------------------------------------------


def optimize_template_closed_loop(
    initial_layout: DocumentLayout,
    ground_truth_pdf_path: Path,
    max_iterations: int = 4,
    target_threshold: float = 0.5,
) -> DocumentLayout:
    """
    Runs an iterative, self-correcting validation loop that adjusts template layout
    and styling parameters dynamically until layout variance drops below the target threshold.

    Gracefully degrades when the optional rendering dependencies (Playwright /
    OpenCV) are not installed: returns the initial layout unchanged so the
    request pipeline still completes.
    """
    try:
        from parser import render_layout_screenshot, calculate_visual_regression
    except ImportError as exc:
        print(f"[Closed-Loop] rendering dependencies unavailable: {exc}")
        return initial_layout

    current_layout = copy.deepcopy(initial_layout)
    work_dir = ground_truth_pdf_path.parent / f"closed_loop_{uuid.uuid4().hex}"
    work_dir.mkdir(parents=True, exist_ok=True)

    # Rasterize baseline target to matching 96 DPI pixel arrays
    gt_image_path = work_dir / "ground_truth_base.png"
    try:
        doc = pymupdf.open(str(ground_truth_pdf_path))
        pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(96 / 72.0, 96 / 72.0), alpha=False)
        pix.save(str(gt_image_path))
        doc.close()
    except Exception as err:
        print(f"[Closed-Loop] ground-truth rasterization failed: {err}")
        return initial_layout

    gt_bytes = gt_image_path.read_bytes()

    for iteration in range(max_iterations):
        cand_path = work_dir / f"candidate_iter_{iteration}.png"
        diff_path = work_dir / f"diff_mask_{iteration}.png"

        # Step A: Capture the visual layout preview screenshot
        try:
            render_layout_screenshot(
                current_layout.model_dump_json(),
                cand_path,
                width=int(current_layout.page_width_px),
                height=int(current_layout.page_height_px),
            )
        except Exception as err:
            print(f"[Closed-Loop] iter {iteration} render failed: {err}")
            break

        if not cand_path.exists():
            break

        # Step B: Perform image regression matching
        try:
            error_score = calculate_visual_regression(gt_image_path, cand_path, diff_path)
            print(f"[Closed-Loop Tracking] Iteration {iteration} Layout Error: {error_score:.4f}%")
        except Exception as err:
            print(f"[Closed-Loop Anomalies] Visual check bypassed: {err}")
            break

        # Step C: Break execution when matching rules hit verification bounds
        if error_score <= target_threshold:
            print("[Closed-Loop Status] Verified template convergence achieved.")
            break

        # Step D: Submit delta images to Critic-Refiner optimization
        cand_bytes = cand_path.read_bytes()
        diff_bytes = diff_path.read_bytes()
        try:
            patch_report = agent_refine_layout_schema(gt_bytes, cand_bytes, diff_bytes, current_layout)
        except Exception as err:
            print(f"[Closed-Loop Status] Prompt sequence dropped: {err}")
            break

        # Step E: Apply precision schema structural changes
        block_map = {b.id: b for b in current_layout.blocks}
        for patch in patch_report.patches:
            if patch.block_id in block_map:
                target_block = block_map[patch.block_id]
                if patch.font_size_change_pt is not None and target_block.style.font_size_pt is not None:
                    target_block.style.font_size_pt = round(
                        target_block.style.font_size_pt + patch.font_size_change_pt, 1
                    )
                if patch.align_patch is not None:
                    target_block.style.align = patch.align_patch
                if patch.font_weight_patch is not None:
                    target_block.style.font_weight = patch.font_weight_patch
                if patch.margin_top_shift_px is not None and target_block.bbox is not None:
                    target_block.bbox.y_px = float(target_block.bbox.y_px + patch.margin_top_shift_px)

        # Step F: Force a geometric auto-layout normalization pass
        current_layout = auto_layout(current_layout)

    return current_layout

