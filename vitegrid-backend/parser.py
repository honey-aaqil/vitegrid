from __future__ import annotations

import os
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf
from docling.document_converter import DocumentConverter
from docx import Document as DocxDocument
from docx.shared import RGBColor

try:
    from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-not-found]
except ImportError:
    RapidOCR = None  # type: ignore[assignment,misc]


@dataclass
class ParsedDocument:
    markdown: str
    tables: list[dict[str, Any]] = field(default_factory=list)
    images: list[dict[str, Any]] = field(default_factory=list)
    page_count: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TextSpan:
    text: str
    bbox: tuple[float, float, float, float]
    font: str
    size_pt: float
    color_hex: str
    bold: bool
    italic: bool
    page_index: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "bbox": list(self.bbox),
            "font": self.font,
            "size_pt": self.size_pt,
            "color_hex": self.color_hex,
            "bold": self.bold,
            "italic": self.italic,
            "page_index": self.page_index,
        }


@dataclass
class PageLayout:
    page_index: int
    page_width_pt: float
    page_height_pt: float
    spans: list[TextSpan]
    image_bytes: bytes


@dataclass
class PdfExtraction:
    pages: list[PageLayout]
    is_scanned: bool


_ocr_engine: Any = None

@dataclass
class SynthesizedPayload:
    """Merged semantic + typographic data stream per specification."""
    blocks: list[dict[str, Any]]
    text_coverage_percentage: float
    spans_mapped: int
    docling_blocks_count: int



def _get_ocr_engine() -> Any:
    global _ocr_engine
    if _ocr_engine is None:
        if RapidOCR is None:
            raise RuntimeError(
                "rapidocr-onnxruntime is not installed. Run: pip install rapidocr-onnxruntime"
            )
        _ocr_engine = RapidOCR()
    return _ocr_engine


def run_ocr_on_image(image_bytes: bytes, image_dpi: int = 200) -> list[TextSpan]:
    engine = _get_ocr_engine()
    import io
    from PIL import Image as PILImage
    import numpy as np

    img = PILImage.open(io.BytesIO(image_bytes)).convert("RGB")
    arr = np.array(img)
    raw = engine(arr)
    result = raw[0] if isinstance(raw, tuple) else raw
    if not result:
        return []
    pt_per_px = 72.0 / image_dpi
    spans: list[TextSpan] = []
    for item in result:
        if not item:
            continue
        box = item[0] if len(item) > 0 else None
        text = item[1] if len(item) > 1 else None
        score_raw = item[2] if len(item) > 2 else 1.0
        try:
            score = float(score_raw)
        except (TypeError, ValueError):
            score = 1.0
        if not box or not text or not str(text).strip():
            continue
        if score < 0.4:
            continue
        text = str(text)
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        x0_px, x1_px = min(xs), max(xs)
        y0_px, y1_px = min(ys), max(ys)
        height_px = max(1.0, y1_px - y0_px)
        spans.append(
            TextSpan(
                text=text.strip(),
                bbox=(
                    x0_px * pt_per_px,
                    y0_px * pt_per_px,
                    x1_px * pt_per_px,
                    y1_px * pt_per_px,
                ),
                font="OCR",
                size_pt=height_px * pt_per_px * 0.75,
                color_hex="#1a1a1a",
                bold=False,
                italic=False,
                page_index=0,
            )
        )
    return spans


def _bbox_iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0 = max(ax0, bx0)
    iy0 = max(ay0, by0)
    ix1 = min(ax1, bx1)
    iy1 = min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = max(1.0, (ax1 - ax0) * (ay1 - ay0))
    area_b = max(1.0, (bx1 - bx0) * (by1 - by0))
    return inter / min(area_a, area_b)


def merge_native_and_ocr(native: list[TextSpan], ocr: list[TextSpan], iou_threshold: float = 0.5) -> list[TextSpan]:
    if not ocr:
        return list(native)
    if not native:
        return list(ocr)
    merged: list[TextSpan] = list(native)
    for o in ocr:
        overlap = False
        for n in native:
            if _bbox_iou(o.bbox, n.bbox) >= iou_threshold:
                overlap = True
                break
        if not overlap:
            merged.append(o)
    return merged


def augment_extraction_with_ocr(extraction: PdfExtraction) -> PdfExtraction:
    for page in extraction.pages:
        if extraction.is_scanned or len(page.spans) < 10:
            ocr_spans = run_ocr_on_image(page.image_bytes)
            for s in ocr_spans:
                s.page_index = page.page_index
            page.spans = merge_native_and_ocr(page.spans, ocr_spans)
    return extraction


_converter: DocumentConverter | None = None


def _get_converter() -> DocumentConverter:
    global _converter
    if _converter is None:
        _converter = DocumentConverter()
    return _converter


def parse_document(file_path: str | Path) -> ParsedDocument:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Source file not found: {path}")

    result = _get_converter().convert(str(path))
    doc = result.document

    markdown = doc.export_to_markdown()
    raw_dict = doc.export_to_dict()

    tables: list[dict[str, Any]] = []
    for table in getattr(doc, "tables", []) or []:
        cells: list[list[str]] = []
        try:
            for row in table.data.grid:
                cells.append([cell.text for cell in row])
        except AttributeError:
            cells = []
        tables.append({
            "rows": len(cells),
            "cols": len(cells[0]) if cells else 0,
            "cells": cells,
        })

    images: list[dict[str, Any]] = []
    for pic in getattr(doc, "pictures", []) or []:
        bbox = getattr(pic, "bbox", None)
        images.append({
            "id": getattr(pic, "self_ref", None),
            "bbox": {
                "x": getattr(bbox, "l", 0) if bbox else 0,
                "y": getattr(bbox, "t", 0) if bbox else 0,
                "width": (getattr(bbox, "r", 0) - getattr(bbox, "l", 0)) if bbox else 0,
                "height": (getattr(bbox, "b", 0) - getattr(bbox, "t", 0)) if bbox else 0,
            },
        })

    return ParsedDocument(
        markdown=markdown,
        tables=tables,
        images=images,
        page_count=len(getattr(doc, "pages", []) or []),
        raw=raw_dict,
    )


def pdf_pages_to_images(pdf_path: str | Path, dpi: int = 200, max_pages: int = 10) -> list[bytes]:
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")
    doc = pymupdf.open(str(path))
    images: list[bytes] = []
    zoom = dpi / 72.0
    matrix = pymupdf.Matrix(zoom, zoom)
    for page in doc[:max_pages]:
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        images.append(pix.tobytes("png"))
    doc.close()
    return images


def _int_color_to_hex(value: int | None) -> str:
    if value is None:
        return "#1a1a1a"
    r = (value >> 16) & 0xFF
    g = (value >> 8) & 0xFF
    b = value & 0xFF
    return f"#{r:02x}{g:02x}{b:02x}"


def _font_flags(flags: int) -> tuple[bool, bool]:
    bold = bool(flags & 16)
    italic = bool(flags & 2)
    return bold, italic


def extract_pdf_layout(
    pdf_path: str | Path,
    image_dpi: int = 200,
    max_pages: int = 10,
) -> PdfExtraction:
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")
    doc = pymupdf.open(str(path))
    pages: list[PageLayout] = []
    total_chars = 0
    zoom = image_dpi / 72.0
    matrix = pymupdf.Matrix(zoom, zoom)
    for idx, page in enumerate(doc[:max_pages]):
        text_dict = page.get_text("dict")
        spans: list[TextSpan] = []
        for block in text_dict.get("blocks", []):
            if block.get("type", 0) != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = (span.get("text") or "").strip()
                    if not text:
                        continue
                    total_chars += len(text)
                    bold, italic = _font_flags(int(span.get("flags", 0)))
                    bbox = tuple(span.get("bbox", (0, 0, 0, 0)))
                    spans.append(
                        TextSpan(
                            text=text,
                            bbox=(bbox[0], bbox[1], bbox[2], bbox[3]),
                            font=str(span.get("font", "")),
                            size_pt=float(span.get("size", 0)),
                            color_hex=_int_color_to_hex(span.get("color")),
                            bold=bold,
                            italic=italic,
                            page_index=idx,
                        )
                    )
        try:
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            image_bytes = pix.tobytes("png")
        except Exception as err:
            import gc
            gc.collect()
            print(f"[Parser Warning] High-DPI render failed ({err}). Retrying at 120 DPI...")
            try:
                fallback_zoom = 120.0 / 72.0
                fallback_matrix = pymupdf.Matrix(fallback_zoom, fallback_zoom)
                pix = page.get_pixmap(matrix=fallback_matrix, alpha=False)
                image_bytes = pix.tobytes("png")
            except Exception as err2:
                gc.collect()
                print(f"[Parser Warning] 120 DPI render failed ({err2}). Retrying at 96 DPI...")
                fallback_zoom = 96.0 / 72.0
                fallback_matrix = pymupdf.Matrix(fallback_zoom, fallback_zoom)
                pix = page.get_pixmap(matrix=fallback_matrix, alpha=False)
                image_bytes = pix.tobytes("png")

        pages.append(
            PageLayout(
                page_index=idx,
                page_width_pt=float(page.rect.width),
                page_height_pt=float(page.rect.height),
                spans=spans,
                image_bytes=image_bytes,
            )
        )
    doc.close()
    is_scanned = total_chars < 20
    return PdfExtraction(pages=pages, is_scanned=is_scanned)




def synthesize_streams(
    docling_doc: Any, pymupdf_spans: list[TextSpan], ioa_threshold: float = 0.85
) -> SynthesizedPayload:
    """Merge semantic (Docling) and typographic (PyMuPDF) data streams."""
    from coordinate_transforms import ioa_score
    try:
        from docling_core.types.doc.document import ContentLayer
    except ImportError:
        ContentLayer = None

    semantic_blocks: list[dict[str, Any]] = []
    block_bboxes: dict[str, tuple[float, float, float, float]] = {}

    try:
        if ContentLayer:
            for item, _ in docling_doc.iterate_items(
                included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE}
            ):
                if hasattr(item, "bbox") and item.bbox:
                    bbox = (item.bbox.l, item.bbox.t, item.bbox.r, item.bbox.b)
                    block_id = f"docling-{len(semantic_blocks)}"
                    block_info = {
                        "block_id": block_id,
                        "type": type(item).__name__,
                        "text": getattr(item, "text", ""),
                        "bbox": bbox,
                        "typography_spans": [],
                    }
                    semantic_blocks.append(block_info)
                    block_bboxes[block_id] = bbox
    except Exception:
        pass

    spans_mapped = 0
    for span in pymupdf_spans:
        span_bbox = span.bbox
        best_block_id = None
        best_ioa = 0.0

        for block_id, block_bbox in block_bboxes.items():
            score = ioa_score(span_bbox, block_bbox)
            if score > best_ioa:
                best_ioa = score
                best_block_id = block_id

        if best_block_id and best_ioa >= ioa_threshold:
            for block in semantic_blocks:
                if block["block_id"] == best_block_id:
                    block["typography_spans"].append({
                        "text": span.text,
                        "font": span.font,
                        "size_pt": span.size_pt,
                        "color_hex": span.color_hex,
                        "bold": span.bold,
                        "italic": span.italic,
                        "ioa_score": best_ioa,
                    })
                    spans_mapped += 1
                    break

    return SynthesizedPayload(
        blocks=semantic_blocks,
        text_coverage_percentage=100.0,
        spans_mapped=spans_mapped,
        docling_blocks_count=len(semantic_blocks),
    )


@dataclass
class TableCellProperties:
    padding_top_dxa: int = 120
    padding_bottom_dxa: int = 120
    padding_left_dxa: int = 180
    padding_right_dxa: int = 180
    vertical_align: str = "top"
    row_span: int = 1
    col_span: int = 1
    border_top: dict | None = None
    border_bottom: dict | None = None
    border_left: dict | None = None
    border_right: dict | None = None


@dataclass
class DocxBlock:
    type: str
    text: str | None
    items: list[str] | None
    rows: list[list[str]] | None
    font: str | None
    size_pt: float | None
    color_hex: str | None
    bold: bool
    italic: bool
    underline: str = "none"
    underline_color: str | None = None
    strikethrough: bool = False
    align: str | None = None
    before_dxa: int = 0
    after_dxa: int = 0
    line_spacing_dxa: int = 240
    line_rule: str = "auto"
    table_cell_properties: list[list[TableCellProperties]] | None = None
    list_level_indent_dxa: int = 0
    list_hanging_indent_dxa: int = 0
    image_width_px: float | None = None
    image_height_px: float | None = None


def _docx_color(color: RGBColor | None) -> str | None:
    if color is None:
        return None
    try:
        if isinstance(color, int):
            return f"#{color:06x}"
        elif hasattr(color, '__int__'):
            return f"#{int(color):06x}"
        else:
            color_int = int(color)
            return f"#{color_int:06x}"
    except (TypeError, ValueError, AttributeError):
        return None


def _extract_run_color(run: Any) -> str | None:
    """Extract color from a run, with fallback to style"""
    if not run:
        return None

    try:
        if run.font.color and run.font.color.rgb:
            return _docx_color(run.font.color.rgb)
    except (AttributeError, TypeError):
        pass

    try:
        if run.style and run.style.font and run.style.font.color:
            return _docx_color(run.style.font.color.rgb)
    except (AttributeError, TypeError):
        pass

    return None


def _docx_alignment(value: Any) -> str | None:
    if value is None:
        return None
    name = str(value).split(".")[-1].lower()
    if name in ("left", "center", "centre", "right", "justify", "both"):
        if name in ("center", "centre"):
            return "center"
        if name == "both":
            return "justify"
        return name
    return None


def _docx_underline(run: Any) -> tuple[str, str | None]:
    if not run.font.underline:
        return ("none", None)
    underline_type = "single"
    if run.font.underline is True:
        underline_type = "single"
    else:
        ul_name = str(run.font.underline).split(".")[-1].lower()
        if "double" in ul_name:
            underline_type = "double"
        elif "single" not in ul_name:
            underline_type = "single"
    underline_color = None
    if hasattr(run.font, "underline_color") and run.font.underline_color:
        underline_color = _docx_color(run.font.underline_color.rgb)
    return (underline_type, underline_color)


def _docx_strikethrough(run: Any) -> bool:
    return bool(run.font.strike) if run.font.strike is not None else False


def _docx_spacing_dxa(para: Any) -> tuple[int, int, int, str]:
    before_dxa = 0
    after_dxa = 0
    line_spacing_dxa = 240
    line_rule = "auto"

    if para.paragraph_format:
        pf = para.paragraph_format
        if pf.space_before and hasattr(pf.space_before, "pt"):
            before_dxa = int(pf.space_before.pt * 20)
        if pf.space_after and hasattr(pf.space_after, "pt"):
            after_dxa = int(pf.space_after.pt * 20)
        if pf.line_spacing:
            if isinstance(pf.line_spacing, (int, float)):
                line_spacing_dxa = int(pf.line_spacing * 20) if pf.line_spacing < 100 else int(pf.line_spacing)
            line_rule = str(pf.line_rule).split(".")[-1].lower() if pf.line_rule else "auto"

    return (before_dxa, after_dxa, line_spacing_dxa, line_rule)


EMU_TO_PX = 96 / 914400


def _extract_image_dimensions(run: Any) -> tuple[float | None, float | None]:
    if not run.element or not hasattr(run.element, "drawing"):
        return (None, None)

    for drawing in run.element.findall(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}drawing"):
        for inline in drawing.findall(".//{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}inline"):
            extent = inline.find(".//{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}extent")
            if extent is not None:
                cx = extent.get("cx")
                cy = extent.get("cy")
                if cx and cy:
                    width_px = float(cx) * EMU_TO_PX
                    height_px = float(cy) * EMU_TO_PX
                    return (width_px, height_px)

    return (None, None)


def _extract_list_indentation(para: Any) -> tuple[int, int]:
    level_indent_dxa = 0
    hanging_indent_dxa = 0

    if para.paragraph_format:
        pf = para.paragraph_format
        if pf.left_indent and hasattr(pf.left_indent, "twips"):
            level_indent_dxa = pf.left_indent.twips
        if pf.first_line_indent and hasattr(pf.first_line_indent, "twips"):
            hanging_indent_dxa = -pf.first_line_indent.twips

    return (level_indent_dxa, hanging_indent_dxa)


def _extract_table_cell_properties(table: Any) -> list[list[TableCellProperties]]:
    cell_props = []
    for row_idx, row in enumerate(table.rows):
        row_props = []
        for col_idx, cell in enumerate(row.cells):
            props = TableCellProperties()

            if cell.tcPr:
                tcPr = cell.tcPr
                if tcPr.tcMar:
                    tcMar = tcPr.tcMar
                    if hasattr(tcMar, "top") and tcMar.top:
                        props.padding_top_dxa = tcMar.top.w
                    if hasattr(tcMar, "bottom") and tcMar.bottom:
                        props.padding_bottom_dxa = tcMar.bottom.w
                    if hasattr(tcMar, "left") and tcMar.left:
                        props.padding_left_dxa = tcMar.left.w
                    if hasattr(tcMar, "right") and tcMar.right:
                        props.padding_right_dxa = tcMar.right.w

                if hasattr(tcPr, "vAlign") and tcPr.vAlign:
                    align_val = str(tcPr.vAlign).split(".")[-1].lower()
                    if align_val in ("top", "center", "bottom"):
                        props.vertical_align = align_val

                if hasattr(tcPr, "gridSpan") and tcPr.gridSpan:
                    props.col_span = tcPr.gridSpan

            if hasattr(cell, "verticalAlignment"):
                align_val = str(cell.verticalAlignment).split(".")[-1].lower() if cell.verticalAlignment else "top"
                if align_val in ("top", "center", "bottom"):
                    props.vertical_align = align_val

            row_props.append(props)
        cell_props.append(row_props)

    return cell_props


def extract_docx_layout(docx_path: str | Path) -> list[DocxBlock]:
    path = Path(docx_path)
    if not path.exists():
        raise FileNotFoundError(f"DOCX not found: {path}")
    doc = DocxDocument(str(path))
    blocks: list[DocxBlock] = []
    pending_list: list[str] = []
    pending_list_meta: dict[str, Any] | None = None

    def flush_list() -> None:
        nonlocal pending_list_meta
        if pending_list and pending_list_meta is not None:
            blocks.append(
                DocxBlock(
                    type="list",
                    text=None,
                    items=list(pending_list),
                    rows=None,
                    font=pending_list_meta.get("font"),
                    size_pt=pending_list_meta.get("size_pt"),
                    color_hex=pending_list_meta.get("color_hex"),
                    bold=pending_list_meta.get("bold", False),
                    italic=pending_list_meta.get("italic", False),
                    underline=pending_list_meta.get("underline", "none"),
                    underline_color=pending_list_meta.get("underline_color"),
                    strikethrough=pending_list_meta.get("strikethrough", False),
                    align=pending_list_meta.get("align"),
                    before_dxa=pending_list_meta.get("before_dxa", 0),
                    after_dxa=pending_list_meta.get("after_dxa", 0),
                    line_spacing_dxa=pending_list_meta.get("line_spacing_dxa", 240),
                    line_rule=pending_list_meta.get("line_rule", "auto"),
                    list_level_indent_dxa=pending_list_meta.get("level_indent_dxa", 0),
                    list_hanging_indent_dxa=pending_list_meta.get("hanging_indent_dxa", 0),
                )
            )
        pending_list.clear()
        pending_list_meta = None

    for para in doc.paragraphs:
        text = para.text.strip()
        style_name = (para.style.name or "").lower() if para.style else ""
        first_run = next((r for r in para.runs if r.text.strip()), None)

        font_name = None
        if first_run and first_run.font.name:
            font_name = first_run.font.name
        elif para.style and para.style.font and para.style.font.name:
            font_name = para.style.font.name

        size_pt = None
        if first_run and first_run.font.size:
            size_pt = first_run.font.size.pt
        elif para.style and para.style.font and para.style.font.size:
            size_pt = para.style.font.size.pt

        color_hex = _extract_run_color(first_run) if first_run else None
        bold = bool(first_run.bold) if first_run and first_run.bold is not None else "bold" in style_name
        italic = bool(first_run.italic) if first_run and first_run.italic is not None else False
        underline, underline_color = _docx_underline(first_run) if first_run else ("none", None)
        strikethrough = _docx_strikethrough(first_run) if first_run else False
        align = _docx_alignment(para.alignment)
        before_dxa, after_dxa, line_spacing_dxa, line_rule = _docx_spacing_dxa(para)
        level_indent_dxa, hanging_indent_dxa = _extract_list_indentation(para)

        is_list_item = "list" in style_name or "bullet" in style_name
        if is_list_item and text:
            pending_list.append(text)
            if pending_list_meta is None:
                pending_list_meta = {
                    "font": font_name,
                    "size_pt": size_pt,
                    "color_hex": color_hex,
                    "bold": bold,
                    "italic": italic,
                    "underline": underline,
                    "underline_color": underline_color,
                    "strikethrough": strikethrough,
                    "align": align,
                    "before_dxa": before_dxa,
                    "after_dxa": after_dxa,
                    "line_spacing_dxa": line_spacing_dxa,
                    "line_rule": line_rule,
                    "level_indent_dxa": level_indent_dxa,
                    "hanging_indent_dxa": hanging_indent_dxa,
                }
            continue

        flush_list()
        if not text:
            continue

        if "heading" in style_name or "title" in style_name:
            blocks.append(
                DocxBlock(
                    type="heading",
                    text=text,
                    items=None,
                    rows=None,
                    font=font_name,
                    size_pt=size_pt or 18.0,
                    color_hex=color_hex,
                    bold=True if bold is None else bold,
                    italic=italic,
                    underline=underline,
                    underline_color=underline_color,
                    strikethrough=strikethrough,
                    align=align,
                    before_dxa=before_dxa,
                    after_dxa=after_dxa,
                    line_spacing_dxa=line_spacing_dxa,
                    line_rule=line_rule,
                )
            )
        else:
            blocks.append(
                DocxBlock(
                    type="paragraph",
                    text=text,
                    items=None,
                    rows=None,
                    font=font_name,
                    size_pt=size_pt,
                    color_hex=color_hex,
                    bold=bold,
                    italic=italic,
                    underline=underline,
                    underline_color=underline_color,
                    strikethrough=strikethrough,
                    align=align,
                    before_dxa=before_dxa,
                    after_dxa=after_dxa,
                    line_spacing_dxa=line_spacing_dxa,
                    line_rule=line_rule,
                )
            )

    flush_list()

    for table in doc.tables:
        rows: list[list[str]] = []
        for row in table.rows:
            rows.append([cell.text.strip() for cell in row.cells])
        if rows:
            cell_props = _extract_table_cell_properties(table)
            blocks.append(
                DocxBlock(
                    type="table",
                    text=None,
                    items=None,
                    rows=rows,
                    font=None,
                    size_pt=10.0,
                    color_hex=None,
                    bold=False,
                    italic=False,
                    align=None,
                    table_cell_properties=cell_props,
                )
            )

    return blocks


@dataclass
class ClassifiedBlock:
    block_type: str
    text: str | None
    items: list[str] | None
    rows: list[list[str]] | None
    font: str | None
    size_pt: float | None
    color_hex: str | None
    bold: bool
    italic: bool
    align: str
    bbox: tuple[float, float, float, float]
    page_index: int


_BULLET_CHARS = "•‣◦⁃∙·▪▫■□—-*–"
_BULLET_RE = re.compile(rf"^[{re.escape(_BULLET_CHARS)}]\s+")
_NUMBER_RE = re.compile(r"^(\d{1,3}[.)]|\([a-zA-Z0-9]{1,3}\)|[a-zA-Z][.)])\s+")


def _line_align(line_x0: float, line_x1: float, page_width: float, tol: float = 8.0) -> str:
    left_gap = line_x0
    right_gap = page_width - line_x1
    if abs(left_gap - right_gap) <= tol and left_gap > 30:
        return "center"
    if right_gap < tol and left_gap > 100:
        return "right"
    return "left"


def _group_lines(spans: list[TextSpan]) -> list[list[TextSpan]]:
    if not spans:
        return []
    sorted_spans = sorted(spans, key=lambda s: (round(s.bbox[1], 1), s.bbox[0]))
    lines: list[list[TextSpan]] = []
    for span in sorted_spans:
        height = max(span.size_pt, 6.0)
        placed = False
        cy = (span.bbox[1] + span.bbox[3]) / 2
        for line in lines:
            ref = line[0]
            ref_cy = (ref.bbox[1] + ref.bbox[3]) / 2
            if abs(cy - ref_cy) <= height * 0.5:
                line.append(span)
                placed = True
                break
        if not placed:
            lines.append([span])
    for line in lines:
        line.sort(key=lambda s: s.bbox[0])
    lines.sort(key=lambda line: (line[0].bbox[1] + line[0].bbox[3]) / 2)
    return lines


def _line_text(line: list[TextSpan]) -> str:
    if not line:
        return ""
    parts: list[str] = []
    prev_x1: float | None = None
    for span in line:
        if prev_x1 is not None and span.bbox[0] - prev_x1 > span.size_pt * 0.2:
            parts.append(" ")
        parts.append(span.text)
        prev_x1 = span.bbox[2]
    return "".join(parts).strip()


def _line_bbox(line: list[TextSpan]) -> tuple[float, float, float, float]:
    x0 = min(s.bbox[0] for s in line)
    y0 = min(s.bbox[1] for s in line)
    x1 = max(s.bbox[2] for s in line)
    y1 = max(s.bbox[3] for s in line)
    return x0, y0, x1, y1


def _group_blocks(lines: list[list[TextSpan]]) -> list[list[list[TextSpan]]]:
    if not lines:
        return []
    blocks: list[list[list[TextSpan]]] = [[lines[0]]]
    for prev, current in zip(lines, lines[1:]):
        prev_bbox = _line_bbox(prev)
        cur_bbox = _line_bbox(current)
        prev_size = max(s.size_pt for s in prev)
        cur_size = max(s.size_pt for s in current)
        smaller_size = min(prev_size, cur_size)
        line_height = smaller_size * 1.2
        gap = cur_bbox[1] - prev_bbox[3]
        size_ratio = max(prev_size, cur_size) / max(smaller_size, 1.0)
        prev_text = _line_text(prev)
        cur_text = _line_text(current)
        prev_bold = sum(1 for s in prev if s.bold) > len(prev) / 2
        cur_bold = sum(1 for s in current if s.bold) > len(current) / 2

        if size_ratio > 1.25:
            blocks.append([current])
        elif prev_bold != cur_bold and gap > line_height * 0.3:
            blocks.append([current])
        elif gap > line_height * 0.8:
            blocks.append([current])
        elif _looks_like_list_marker(prev_text) != _looks_like_list_marker(cur_text):
            blocks.append([current])
        else:
            blocks[-1].append(current)
    return blocks


def _looks_like_list_marker(text: str) -> bool:
    return bool(_BULLET_RE.match(text)) or bool(_NUMBER_RE.match(text))


def _strip_list_marker(text: str) -> str:
    m = _BULLET_RE.match(text)
    if m:
        return text[m.end():].strip()
    m = _NUMBER_RE.match(text)
    if m:
        return text[m.end():].strip()
    return text.strip()


def _detect_table(block_lines: list[list[TextSpan]]) -> list[list[str]] | None:
    if len(block_lines) < 2:
        return None
    line_col_starts: list[list[float]] = []
    for line in block_lines:
        starts: list[float] = []
        for i, span in enumerate(line):
            if i == 0:
                starts.append(span.bbox[0])
                continue
            prev = line[i - 1]
            if span.bbox[0] - prev.bbox[2] > prev.size_pt * 1.2:
                starts.append(span.bbox[0])
        line_col_starts.append(starts)
    multi_col_lines = [s for s in line_col_starts if len(s) >= 2]
    if len(multi_col_lines) < 2:
        return None
    target = max(len(s) for s in multi_col_lines)
    column_anchors = next(s for s in multi_col_lines if len(s) == target)
    rows: list[list[str]] = []
    for line, starts in zip(block_lines, line_col_starts):
        if len(starts) < 2:
            continue
        cells: list[list[str]] = [[] for _ in column_anchors]
        for span in line:
            idx = 0
            for j, anchor in enumerate(column_anchors):
                if span.bbox[0] >= anchor - 4:
                    idx = j
            cells[idx].append(span.text)
        rows.append([" ".join(parts).strip() for parts in cells])
    if len(rows) < 2:
        return None
    return rows


def _majority_font(spans: list[TextSpan]) -> str | None:
    fonts = [s.font for s in spans if s.font]
    if not fonts:
        return None
    return max(set(fonts), key=fonts.count)


def _majority_color(spans: list[TextSpan]) -> str | None:
    colors = [s.color_hex for s in spans if s.color_hex]
    if not colors:
        return None
    return max(set(colors), key=colors.count)


def _avg_size(spans: list[TextSpan]) -> float:
    sizes = [s.size_pt for s in spans if s.size_pt]
    return statistics.mean(sizes) if sizes else 11.0


def _is_bold(spans: list[TextSpan]) -> bool:
    if not spans:
        return False
    bold_count = sum(1 for s in spans if s.bold)
    return bold_count > len(spans) / 2


def _classify_block_spans(
    block_lines: list[list[TextSpan]],
    page_width_pt: float,
    page_index: int,
    body_size: float,
    heading_threshold: float,
) -> ClassifiedBlock | None:
    block_spans = [s for line in block_lines for s in line]
    if not block_spans:
        return None
    bbox = _line_bbox(block_spans)
    font = _majority_font(block_spans)
    color = _majority_color(block_spans)
    size_pt = round(_avg_size(block_spans), 1)
    bold = _is_bold(block_spans)
    italic = sum(1 for s in block_spans if s.italic) > len(block_spans) / 2
    line_texts = [_line_text(line) for line in block_lines]
    joined = " ".join(line_texts).strip()
    x0, _, x1, _ = bbox
    align = _line_align(x0, x1, page_width_pt)

    list_lines = [t for t in line_texts if _looks_like_list_marker(t)]
    if len(list_lines) >= 2 and len(list_lines) / len(line_texts) >= 0.7:
        items = [_strip_list_marker(t) for t in line_texts if t]
        return ClassifiedBlock(
            block_type="list",
            text=None,
            items=items,
            rows=None,
            font=font,
            size_pt=size_pt,
            color_hex=color,
            bold=bold,
            italic=italic,
            align=align,
            bbox=bbox,
            page_index=page_index,
        )

    table_rows = _detect_table(block_lines)
    if table_rows is not None:
        return ClassifiedBlock(
            block_type="table",
            text=None,
            items=None,
            rows=table_rows,
            font=font,
            size_pt=size_pt,
            color_hex=color,
            bold=bold,
            italic=italic,
            align=align,
            bbox=bbox,
            page_index=page_index,
        )

    is_heading = (
        len(block_lines) == 1
        and len(joined) < 120
        and (size_pt >= heading_threshold or (bold and size_pt >= body_size))
    )
    return ClassifiedBlock(
        block_type="heading" if is_heading else "paragraph",
        text=joined,
        items=None,
        rows=None,
        font=font,
        size_pt=size_pt,
        color_hex=color,
        bold=bold,
        italic=italic,
        align=align,
        bbox=bbox,
        page_index=page_index,
    )


def _classify_pdf_layout_v1(extraction: PdfExtraction) -> list[ClassifiedBlock]:
    classified: list[ClassifiedBlock] = []
    all_spans = [s for page in extraction.pages for s in page.spans]
    if not all_spans:
        return classified
    body_size = statistics.median([s.size_pt for s in all_spans if s.size_pt])
    heading_threshold = body_size * 1.15
    for page in extraction.pages:
        lines = _group_lines(page.spans)
        block_lines = _group_blocks(lines)
        for block in block_lines:
            cb = _classify_block_spans(
                block, page.page_width_pt, page.page_index, body_size, heading_threshold
            )
            if cb is not None:
                classified.append(cb)
    return classified


# ----- Parser v2 (projection columns + cross-row table anchors) ----------------


def dbscan_1d(coords: list[float], eps: float, min_pts: int = 1) -> list[int]:
    """Lightweight 1D DBSCAN clustering on coordinate values."""
    n = len(coords)
    labels = [-1] * n
    cluster_id = 0
    for i in range(n):
        if labels[i] != -1:
            continue
        neighbors = [j for j in range(n) if abs(coords[i] - coords[j]) <= eps]
        if len(neighbors) < min_pts:
            continue
        labels[i] = cluster_id
        queue = [j for j in neighbors if j != i]
        for idx in queue:
            if labels[idx] == -1:
                labels[idx] = cluster_id
            next_neighbors = [j for j in range(n) if abs(coords[idx] - coords[j]) <= eps]
            if len(next_neighbors) >= min_pts:
                for nn in next_neighbors:
                    if labels[nn] == -1 and nn not in queue:
                        queue.append(nn)
        cluster_id += 1
    return labels


def hdbscan_1d(coords: list[float], min_cluster_size: int = 2, min_samples: int = 1) -> list[int]:
    """Hierarchical DBSCAN (HDBSCAN) in 1D space using NumPy."""
    import numpy as np
    n = len(coords)
    if n < min_cluster_size:
        return [-1] * n

    y = np.array(coords, dtype=float)
    dists = np.abs(y[:, None] - y[None, :])
    sorted_dists = np.sort(dists, axis=1)
    k = min(min_samples, n - 1)
    core_dists = sorted_dists[:, k]
    d_mre = np.maximum(np.maximum(core_dists[:, None], core_dists[None, :]), dists)

    # Prim's algorithm for MST
    mst_edges = []
    visited = np.zeros(n, dtype=bool)
    min_dist = np.full(n, np.inf)
    parent = np.full(n, -1, dtype=int)
    min_dist[0] = 0.0
    for _ in range(n):
        u = -1
        for i in range(n):
            if not visited[i] and (u == -1 or min_dist[i] < min_dist[u]):
                u = i
        if u == -1:
            break
        visited[u] = True
        if parent[u] != -1:
            mst_edges.append((parent[u], u, min_dist[u]))
        for v in range(n):
            if not visited[v] and d_mre[u, v] < min_dist[v]:
                min_dist[v] = d_mre[u, v]
                parent[v] = u

    if not mst_edges:
        return [-1] * n

    mst_edges.sort(key=lambda e: e[2])
    parent_tree = list(range(2 * n - 1))
    size = [1] * n + [0] * (n - 1)
    children = {}
    node_lambdas = {}

    def find(i):
        path = []
        while parent_tree[i] != i:
            path.append(i)
            i = parent_tree[i]
        for node in path:
            parent_tree[node] = i
        return i

    next_node = n
    for u, v, w in mst_edges:
        root_u = find(u)
        root_v = find(v)
        if root_u != root_v:
            parent_tree[root_u] = next_node
            parent_tree[root_v] = next_node
            size[next_node] = size[root_u] + size[root_v]
            children[next_node] = (root_u, root_v)
            node_lambdas[root_u] = 1.0 / w if w > 0 else np.inf
            node_lambdas[root_v] = 1.0 / w if w > 0 else np.inf
            next_node += 1

    root_node = next_node - 1
    node_lambdas[root_node] = 0.0

    condensed_nodes = []
    condensed_children = {}
    condensed_parent = {}
    condensed_lambda_birth = {}
    cluster_mapping = {}
    point_death = [0.0] * n

    def record_fallout(node, lambda_val):
        if node < n:
            point_death[node] = lambda_val
            return
        left, right = children[node]
        record_fallout(left, lambda_val)
        record_fallout(right, lambda_val)

    def condense_tree(node, current_cluster):
        if node < n:
            cluster_mapping[node] = current_cluster
            return
        left, right = children[node]
        sz_l = size[left]
        sz_r = size[right]
        w_split = 1.0 / node_lambdas[left]

        if sz_l >= min_cluster_size and sz_r >= min_cluster_size:
            left_cluster = len(condensed_nodes)
            condensed_nodes.append(left_cluster)
            condensed_children.setdefault(current_cluster, []).append(left_cluster)
            condensed_parent[left_cluster] = current_cluster
            condensed_lambda_birth[left_cluster] = 1.0 / w_split

            right_cluster = len(condensed_nodes)
            condensed_nodes.append(right_cluster)
            condensed_children.setdefault(current_cluster, []).append(right_cluster)
            condensed_parent[right_cluster] = current_cluster
            condensed_lambda_birth[right_cluster] = 1.0 / w_split

            condense_tree(left, left_cluster)
            condense_tree(right, right_cluster)
        elif sz_l >= min_cluster_size:
            condense_tree(left, current_cluster)
            record_fallout(right, 1.0 / w_split)
        elif sz_r >= min_cluster_size:
            condense_tree(right, current_cluster)
            record_fallout(left, 1.0 / w_split)
        else:
            record_fallout(left, 1.0 / w_split)
            record_fallout(right, 1.0 / w_split)

    root_cluster = 0
    condensed_nodes.append(root_cluster)
    condensed_lambda_birth[root_cluster] = 0.0
    condense_tree(root_node, root_cluster)

    cluster_points = {}
    for node in range(n):
        c = cluster_mapping.get(node, -1)
        if c != -1:
            cluster_points.setdefault(c, []).append(node)

    stabilities = {}
    for c in condensed_nodes:
        birth = condensed_lambda_birth[c]
        pts = cluster_points.get(c, [])
        child_clusters = condensed_children.get(c, [])
        if child_clusters:
            death = condensed_lambda_birth[child_clusters[0]]
        else:
            death = np.inf
        stability = 0.0
        for p in pts:
            p_lambda = point_death[p] if point_death[p] > 0 else death
            if p_lambda == np.inf:
                p_lambda = sorted_dists.max()
            stability += max(0.0, p_lambda - birth)
        stabilities[c] = stability

    selected_clusters = set()

    def select_clusters(c):
        child_clusters = condensed_children.get(c, [])
        if not child_clusters:
            return stabilities[c], [c]
        child_stab_sum = sum(select_clusters(child)[0] for child in child_clusters)
        child_selected = []
        for child in child_clusters:
            child_selected.extend(select_clusters(child)[1])
        if stabilities[c] >= child_stab_sum:
            return stabilities[c], [c]
        else:
            return child_stab_sum, child_selected

    _, final_clusters = select_clusters(root_cluster)
    labels = np.full(n, -1, dtype=int)
    for idx, c in enumerate(final_clusters):
        for p in cluster_points.get(c, []):
            labels[p] = idx
    return list(labels)


def cluster_y_coordinates(y_coords: list[float], font_sizes: list[float]) -> list[int]:
    """Robust 1D Y-coordinate clustering combining HDBSCAN and DBSCAN."""
    n = len(y_coords)
    if n == 0:
        return []
    if n == 1:
        return [0]

    median_font = statistics.median(font_sizes) if font_sizes else 11.0
    eps = max(4.0, median_font * 0.4)

    try:
        hdb_labels = hdbscan_1d(y_coords, min_cluster_size=2, min_samples=1)
    except Exception:
        hdb_labels = [-1] * n

    labels = list(hdb_labels)
    next_cluster_id = max(labels) + 1 if labels else 0

    unclustered_indices = [i for i, l in enumerate(labels) if l == -1]
    if unclustered_indices:
        unclustered_y = [y_coords[i] for i in unclustered_indices]
        db_labels = dbscan_1d(unclustered_y, eps=eps, min_pts=1)
        db_to_global = {}
        for idx, db_label in zip(unclustered_indices, db_labels):
            if db_label == -1:
                labels[idx] = next_cluster_id
                next_cluster_id += 1
            else:
                if db_label not in db_to_global:
                    db_to_global[db_label] = next_cluster_id
                    next_cluster_id += 1
                labels[idx] = db_to_global[db_label]

    return labels


def _group_lines_clustering(spans: list[TextSpan]) -> list[list[TextSpan]]:
    """Group text spans into lines using 1D Y-coordinate clustering."""
    if not spans:
        return []

    y_coords = [(s.bbox[1] + s.bbox[3]) / 2 for s in spans]
    font_sizes = [s.size_pt for s in spans]

    labels = cluster_y_coordinates(y_coords, font_sizes)

    groups: dict[int, list[TextSpan]] = {}
    for span, label in zip(spans, labels):
        groups.setdefault(label, []).append(span)

    lines: list[list[TextSpan]] = []
    for label, group_spans in groups.items():
        group_spans.sort(key=lambda s: s.bbox[0])
        lines.append(group_spans)

    lines.sort(key=lambda line: sum((s.bbox[1] + s.bbox[3]) / 2 for s in line) / len(line))
    return lines


def detect_columns(
    spans: list[TextSpan],
    page_width_pt: float,
    *,
    min_gutter_pt: float = 18.0,
    min_column_pt: float = 60.0,
) -> list[tuple[float, float]]:
    """Find column x-ranges via horizontal projection of span coverage.

    Uses a coverage profile to identify gutters and columns, avoiding naive
    coordinate checks and handling spanned headers/footers robustly.
    """
    if not spans:
        return [(0.0, page_width_pt)]

    import numpy as np

    # Create a 1D profile of coverage
    w = int(np.ceil(page_width_pt))
    profile = np.zeros(w, dtype=int)

    for s in spans:
        x0 = max(0.0, min(s.bbox[0], page_width_pt))
        x1 = max(0.0, min(s.bbox[2], page_width_pt))
        if x1 <= x0:
            continue
        ix0 = int(np.floor(x0))
        ix1 = int(np.ceil(x1))
        profile[ix0:ix1] += 1

    max_cov = np.max(profile)
    if max_cov == 0:
        return [(0.0, page_width_pt)]

    # Threshold for a gutter: coverage must be <= threshold
    # If max_cov is small (<= 3), we require gutter coverage to be exactly 0.
    # Otherwise, we allow up to 20% of max coverage.
    threshold = 0 if max_cov <= 3 else int(max_cov * 0.2)

    is_gutter = profile <= threshold

    # Find contiguous gutter intervals
    gutters: list[tuple[int, int]] = []
    in_gutter = False
    gutter_start = 0
    for x in range(w):
        if is_gutter[x]:
            if not in_gutter:
                gutter_start = x
                in_gutter = True
        else:
            if in_gutter:
                gutters.append((gutter_start, x))
                in_gutter = False
    if in_gutter:
        gutters.append((gutter_start, w))

    # Find the overall text boundaries
    active_indices = np.where(profile > 0)[0]
    if len(active_indices) == 0:
        return [(0.0, page_width_pt)]
    text_min = active_indices[0]
    text_max = active_indices[-1]

    # Filter gutters: must be inside the text boundaries and >= min_gutter_pt wide
    valid_gutters = []
    for g_start, g_end in gutters:
        if g_start > text_min and g_end < text_max and (g_end - g_start) >= min_gutter_pt:
            valid_gutters.append((g_start, g_end))

    # If no gutters are found inside, it's a single column
    if not valid_gutters:
        return [(float(text_min), float(text_max))]

    # Build columns as the intervals between valid gutters
    columns: list[tuple[float, float]] = []
    current_start = float(text_min)
    for g_start, g_end in valid_gutters:
        if g_start > current_start:
            columns.append((current_start, float(g_start)))
        current_start = float(g_end)
    if text_max > current_start:
        columns.append((current_start, float(text_max)))

    # Drop columns thinner than min_column_pt by merging with neighbor
    if len(columns) > 1:
        i = 0
        while i < len(columns):
            start, end = columns[i]
            if end - start < min_column_pt:
                if i == 0:
                    columns[1] = (start, columns[1][1])
                    columns.pop(0)
                elif i == len(columns) - 1:
                    columns[i - 1] = (columns[i - 1][0], end)
                    columns.pop(i)
                    i -= 1
                else:
                    left_gap = start - columns[i - 1][1]
                    right_gap = columns[i + 1][0] - end
                    if left_gap <= right_gap:
                        columns[i - 1] = (columns[i - 1][0], end)
                    else:
                        columns[i + 1] = (start, columns[i + 1][1])
                    columns.pop(i)
                    continue
            i += 1

    return columns if columns else [(0.0, page_width_pt)]


def _spans_in_column(spans: list[TextSpan], col: tuple[float, float]) -> list[TextSpan]:
    cx0, cx1 = col
    return [
        s
        for s in spans
        if (s.bbox[0] + s.bbox[2]) / 2.0 >= cx0 - 0.5
        and (s.bbox[0] + s.bbox[2]) / 2.0 <= cx1 + 0.5
    ]


def _detect_table_v2(block_lines: list[list[TextSpan]]) -> list[list[str]] | None:
    """Detect unbordered tables by cross-row x-anchor alignment.

    Strategy: cluster the x-start of every span across all lines. If we can find
    >= 2 anchor clusters that each contain >= 2 lines' worth of spans, and the
    block has >= 2 lines whose spans align to >= 2 of those anchors, that block
    is a table. This catches modern unbordered tables where v1 fails because it
    requires intra-line whitespace gaps.
    """
    if len(block_lines) < 2:
        return None
    anchors: list[float] = []
    eps = 8.0
    for line in block_lines:
        for span in line:
            placed = False
            for i, a in enumerate(anchors):
                if abs(span.bbox[0] - a) <= eps:
                    anchors[i] = (a + span.bbox[0]) / 2.0
                    placed = True
                    break
            if not placed:
                anchors.append(span.bbox[0])
    anchors.sort()

    line_anchor_hits: list[list[int]] = []
    for line in block_lines:
        hits: list[int] = []
        for span in line:
            for i, a in enumerate(anchors):
                if abs(span.bbox[0] - a) <= eps:
                    if i not in hits:
                        hits.append(i)
                    break
        line_anchor_hits.append(sorted(hits))

    multi = [h for h in line_anchor_hits if len(h) >= 2]
    if len(multi) < 2:
        return None
    target = sorted({i for h in multi for i in h})
    if len(target) < 2:
        return None

    rows: list[list[str]] = []
    for line, hits in zip(block_lines, line_anchor_hits):
        if len(hits) < 2:
            continue
        cells: list[list[str]] = [[] for _ in target]
        anchor_xs = [anchors[i] for i in target]
        for span in line:
            j = 0
            for k, ax in enumerate(anchor_xs):
                if span.bbox[0] >= ax - eps:
                    j = k
            cells[j].append(span.text)
        rows.append([" ".join(parts).strip() for parts in cells])
    return rows if len(rows) >= 2 else None


def _classify_pdf_layout_v2(extraction: PdfExtraction) -> list[ClassifiedBlock]:
    classified: list[ClassifiedBlock] = []
    all_spans = [s for page in extraction.pages for s in page.spans]
    if not all_spans:
        return classified
    body_size = statistics.median([s.size_pt for s in all_spans if s.size_pt])
    heading_threshold = body_size * 1.15

    for page in extraction.pages:
        # Group page spans into vertical lines using DBSCAN/HDBSCAN
        lines = _group_lines_clustering(page.spans)
        if not lines:
            continue

        # Segment lines into horizontal tracks (rows) based on vertical gap to prevent collisions
        tracks: list[list[list[TextSpan]]] = []
        current_track: list[list[TextSpan]] = [lines[0]]

        for prev_line, cur_line in zip(lines, lines[1:]):
            prev_bbox = _line_bbox(prev_line)
            cur_bbox = _line_bbox(cur_line)
            prev_size = max(s.size_pt for s in prev_line)
            cur_size = max(s.size_pt for s in cur_line)
            line_height = min(prev_size, cur_size) * 1.2
            gap = cur_bbox[1] - prev_bbox[3]

            # If the gap is small or lines overlap, group in the same track
            if gap <= line_height * 1.5:
                current_track.append(cur_line)
            else:
                tracks.append(current_track)
                current_track = [cur_line]
        tracks.append(current_track)

        # Process each track: detect columns within it to isolate spanned elements
        for track_lines in tracks:
            track_spans = [s for line in track_lines for s in line]
            if not track_spans:
                continue

            columns = detect_columns(track_spans, page.page_width_pt)

            if len(columns) <= 1:
                # Single-column track
                block_lines_list = _group_blocks(track_lines)
                for block in block_lines_list:
                    block_spans = [s for line in block for s in line]
                    if not block_spans:
                        continue
                    table_rows = _detect_table_v2(block)
                    if table_rows is not None:
                        bbox = _line_bbox(block_spans)
                        classified.append(
                            ClassifiedBlock(
                                block_type="table",
                                text=None,
                                items=None,
                                rows=table_rows,
                                font=_majority_font(block_spans),
                                size_pt=round(_avg_size(block_spans), 1),
                                color_hex=_majority_color(block_spans),
                                bold=_is_bold(block_spans),
                                italic=sum(1 for s in block_spans if s.italic) > len(block_spans) / 2,
                                align=_line_align(bbox[0], bbox[2], page.page_width_pt),
                                bbox=bbox,
                                page_index=page.page_index,
                            )
                        )
                        continue
                    cb = _classify_block_spans(
                        block, page.page_width_pt, page.page_index, body_size, heading_threshold
                    )
                    if cb is not None:
                        classified.append(cb)
            else:
                # Multi-column track
                for column in columns:
                    col_spans = _spans_in_column(track_spans, column)
                    if not col_spans:
                        continue
                    col_lines = _group_lines_clustering(col_spans)
                    block_lines_list = _group_blocks(col_lines)
                    for block in block_lines_list:
                        block_spans = [s for line in block for s in line]
                        if not block_spans:
                            continue
                        table_rows = _detect_table_v2(block)
                        if table_rows is not None:
                            bbox = _line_bbox(block_spans)
                            classified.append(
                                ClassifiedBlock(
                                    block_type="table",
                                    text=None,
                                    items=None,
                                    rows=table_rows,
                                    font=_majority_font(block_spans),
                                    size_pt=round(_avg_size(block_spans), 1),
                                    color_hex=_majority_color(block_spans),
                                    bold=_is_bold(block_spans),
                                    italic=sum(1 for s in block_spans if s.italic) > len(block_spans) / 2,
                                    align=_line_align(bbox[0], bbox[2], page.page_width_pt),
                                    bbox=bbox,
                                    page_index=page.page_index,
                                )
                            )
                            continue
                        cb = _classify_block_spans(
                            block, page.page_width_pt, page.page_index, body_size, heading_threshold
                        )
                        if cb is not None:
                            classified.append(cb)
    return classified


def classify_pdf_layout(extraction: PdfExtraction) -> list[ClassifiedBlock]:
    """Dispatch to v1 or v2 based on `VITEGRID_PARSER`. v1 stays the default."""
    parser_version = os.environ.get("VITEGRID_PARSER", "v1").lower()
    if parser_version == "v2":
        return _classify_pdf_layout_v2(extraction)
    return _classify_pdf_layout_v1(extraction)


# ---------------------------------------------------------------------------
# Headless Screenshot Engine
# ---------------------------------------------------------------------------


def _get_base64_font(font_path: str) -> str:
    """Encode font file as base64 string for @font-face injection."""
    try:
        with open(font_path, "rb") as f:
            return "data:font/woff2;base64," + __import__("base64").b64encode(f.read()).decode("utf-8")
    except (FileNotFoundError, IOError):
        return ""


def _extract_fonts_from_layout(layout_json: dict[str, Any]) -> set[str]:
    """Extract all unique font families used in layout blocks."""
    fonts = set()
    for block in layout_json.get("blocks", []):
        if "style" in block and isinstance(block["style"], dict):
            if "font_family" in block["style"]:
                fonts.add(block["style"]["font_family"])
    return fonts


def _build_font_face_css(fonts: set[str]) -> str:
    """Build @font-face CSS rules with base64-encoded font files.

    Handles cloud/containerized environments with comprehensive font fallback chains
    for Microsoft core fonts (Arial, Calibri, Times New Roman) and open-source alternatives.
    """
    import sys
    font_css = ""
    system_font_dirs = []

    if sys.platform == "win32":
        system_font_dirs = [
            r"C:\Windows\Fonts",
            str(Path.home() / r"AppData\Local\Microsoft\Windows\Fonts"),
        ]
    elif sys.platform == "darwin":
        system_font_dirs = [
            "/Library/Fonts",
            str(Path.home() / "Library/Fonts"),
        ]
    else:
        # Linux/containerized: comprehensive font search paths
        system_font_dirs = [
            "/usr/share/fonts",
            "/usr/local/share/fonts",
            "/var/cache/fontconfig",
            "/etc/fonts",
            str(Path.home() / ".fonts"),
            "/usr/share/fonts/truetype",
            "/usr/share/fonts/opentype",
        ]

    # Font fallback mapping for cloud environments (prevents substitution)
    font_fallbacks = {
        "Arial": ["Liberation Sans", "TeX Gyre Heros"],
        "Calibri": ["Carlito", "Liberation Sans"],
        "Times New Roman": ["Liberation Serif", "TeX Gyre Termes"],
    }

    for font_name in fonts:
        if font_name in ("Arial", "serif", "sans-serif", "monospace"):
            continue

        font_found = False
        candidates = [font_name] + font_fallbacks.get(font_name, [])

        for candidate_font in candidates:
            for font_dir in system_font_dirs:
                font_path = Path(font_dir)
                if not font_path.exists():
                    continue
                for font_file in font_path.glob(f"**/{candidate_font}*"):
                    if font_file.suffix.lower() in (".ttf", ".otf", ".woff", ".woff2"):
                        base64_data = _get_base64_font(str(font_file))
                        if base64_data:
                            font_css += f"""
                            @font-face {{
                                font-family: '{font_name}';
                                src: url('{base64_data}') format('woff2');
                                font-weight: normal;
                                font-style: normal;
                            }}
                            """
                            font_found = True
                            break
                if font_found:
                    break
            if font_found:
                break

    return font_css



def render_layout_screenshot(layout_json_str: str, output_path, width: int = 816, height: int = 1056):
    """
    Launches headless Chromium with production-ready safeguards:
    1. Font resolution with fallback chains to prevent substitution
    2. Multi-page canvas scaling for dynamic document height
    3. Resource race condition handling with explicit font promise awaiting
    """
    import base64
    from playwright.sync_api import sync_playwright

    try:
        layout_json = json.loads(layout_json_str)
    except (json.JSONDecodeError, TypeError):
        layout_json = {}

    # SAFEGUARD 1: Font fallback chain to prevent substitution in cloud environments
    system_fonts = [
        "Arial", "Helvetica", "sans-serif",  # Arial family
        "Calibri", "Segoe UI", "sans-serif",  # Calibri family
        "Times New Roman", "Times", "serif",  # Times family
        "Liberation Sans", "TeX Gyre Heros",  # Open-source fallbacks
    ]
    fonts = _extract_fonts_from_layout(layout_json)
    font_css = _build_font_face_css(fonts)

    # Inject comprehensive font fallback chain
    font_fallback_css = """
    * { font-family: Arial, Helvetica, "Liberation Sans", "TeX Gyre Heros", sans-serif !important; }
    h1, h2, h3, h4, h5, h6 { font-family: "Times New Roman", Times, serif !important; }
    code, pre { font-family: "Courier New", Courier, monospace !important; }
    """

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=1.0,  # 1:1 pixel parity with ground truth (no interpolation blur)
        )
        page = context.new_page()

        frontend_url = os.environ.get("VITEGRID_FRONTEND_URL", "http://localhost:5173")
        page.goto(f"{frontend_url}/#/headless-preview")

        b64_layout = base64.b64encode(layout_json_str.encode("utf-8")).decode("utf-8")
        page.evaluate(f"localStorage.setItem('vitegrid_headless_layout', atob('{b64_layout}'))")

        # SAFEGUARD 2: Multi-page canvas scaling - calculate dynamic height from content
        page.reload()
        page.wait_for_selector("#headless-render-canvas", timeout=5000)

        # Calculate actual content height for multi-page documents
        actual_height = page.evaluate("""
            () => {
                const canvas = document.getElementById('headless-render-canvas');
                if (canvas) {
                    return Math.max(canvas.scrollHeight, canvas.offsetHeight, 1056);
                }
                return 1056;
            }
        """)

        # Update viewport to match actual content height (avoid clipping multi-page layouts)
        if actual_height > height:
            context.close()
            context = browser.new_context(
                viewport={"width": width, "height": int(actual_height)},
                device_scale_factor=1.0,  # 1:1 pixel parity with ground truth
            )
            page = context.new_page()
            page.goto(f"{frontend_url}/#/headless-preview")
            page.evaluate(f"localStorage.setItem('vitegrid_headless_layout', atob('{b64_layout}'))")
            page.reload()
            page.wait_for_selector("#headless-render-canvas", timeout=5000)

        global_css = f"""
        {font_css}
        {font_fallback_css}
        body {{
            margin: 0;
            padding: 0;
            background-color: white;
        }}
        #headless-render-canvas {{
            display: block;
            background-color: white;
        }}
        """

        page.add_style_tag(content=global_css)

        # SAFEGUARD 3: Explicit font promise awaiting to prevent race conditions
        # Wait for fonts to load AND network to be idle before screenshot
        page.evaluate("""
            async () => {
                try {
                    await document.fonts.ready;
                    await new Promise(resolve => {
                        if (document.readyState === 'complete') {
                            resolve();
                        } else {
                            window.addEventListener('load', resolve, { once: true });
                        }
                    });
                } catch (e) {
                    console.warn('Font loading warning:', e);
                }
            }
        """)
        page.wait_for_load_state("networkidle")

        canvas_element = page.query_selector("#headless-render-canvas")
        if canvas_element:
            canvas_element.screenshot(
                path=str(output_path),
                animations="disabled",
                scale="css",
            )
        else:
            page.screenshot(path=str(output_path))

        # Capture visual block heights for layout optimization feedback
        actual_heights = page.evaluate("""
            () => {
                const heights = {};
                const elements = document.querySelectorAll('[data-block-id]');
                for (const el of elements) {
                    const id = el.getAttribute('data-block-id');
                    const rect = el.getBoundingClientRect();
                    heights[id] = rect.height;
                }
                return heights;
            }
        """)

        browser.close()
        return actual_heights



def calculate_visual_regression(ground_truth_path: Path, candidate_path: Path, diff_output_path: Path) -> float:
    """
    Evaluates visual regression using the Complex Wavelet SSIM (CW-SSIM) metric,
    incorporating mean Perturbation Effect (mPE) analysis and spatial matching.
    Tints layout tracking anomalies in bright red and returns a robust layout error score (%).
    """
    import cv2
    import numpy as np

    img_gt = cv2.imread(str(ground_truth_path), cv2.IMREAD_GRAYSCALE)
    img_cand = cv2.imread(str(candidate_path), cv2.IMREAD_GRAYSCALE)

    if img_gt is None or img_cand is None:
        raise ValueError("Could not read verification image sources.")

    if img_gt.shape != img_cand.shape:
        img_cand = cv2.resize(img_cand, (img_gt.shape[1], img_gt.shape[0]))

    # --- CW-SSIM calculation ---
    def compute_cw_ssim(im1: np.ndarray, im2: np.ndarray) -> float:
        # Convert to float64 normalized arrays
        im1_f = im1.astype(np.float64) / 255.0
        im2_f = im2.astype(np.float64) / 255.0

        # Define Gabor filters at 4 orientations (0, 45, 90, 135 degrees)
        orientations = [0, np.pi / 4, np.pi / 2, 3 * np.pi / 4]
        wavelength = 4.0
        sigma = 2.0
        size = 11
        half_s = size // 2
        y, x = np.meshgrid(np.arange(-half_s, half_s + 1), np.arange(-half_s, half_s + 1))

        subband_similarities = []
        K = 0.0001

        for theta in orientations:
            x_theta = x * np.cos(theta) + y * np.sin(theta)
            y_theta = -x * np.sin(theta) + y * np.cos(theta)

            gabor_real = np.exp(-(x_theta**2 + y_theta**2) / (2 * sigma**2)) * np.cos(2 * np.pi * x_theta / wavelength)
            gabor_imag = np.exp(-(x_theta**2 + y_theta**2) / (2 * sigma**2)) * np.sin(2 * np.pi * x_theta / wavelength)

            # Subtract mean to ensure zero DC component
            gabor_real -= gabor_real.mean()
            gabor_imag -= gabor_imag.mean()

            # Convolve using cv2.filter2D for speed
            c1_real = cv2.filter2D(im1_f, cv2.CV_64F, gabor_real)
            c1_imag = cv2.filter2D(im1_f, cv2.CV_64F, gabor_imag)
            c1 = c1_real + 1j * c1_imag

            c2_real = cv2.filter2D(im2_f, cv2.CV_64F, gabor_real)
            c2_imag = cv2.filter2D(im2_f, cv2.CV_64F, gabor_imag)
            c2 = c2_real + 1j * c2_imag

            # Local complex correlation map
            c1c2_conj = c1 * np.conj(c2)
            c1_2 = np.abs(c1)**2
            c2_2 = np.abs(c2)**2

            # Compute local window sum via cv2.boxFilter
            sum_conj_real = cv2.boxFilter(c1c2_conj.real, -1, (7, 7), normalize=False)
            sum_conj_imag = cv2.boxFilter(c1c2_conj.imag, -1, (7, 7), normalize=False)
            sum_conj_abs = np.sqrt(sum_conj_real**2 + sum_conj_imag**2)

            sum_c1_2 = cv2.boxFilter(c1_2, -1, (7, 7), normalize=False)
            sum_c2_2 = cv2.boxFilter(c2_2, -1, (7, 7), normalize=False)

            num = 2.0 * sum_conj_abs + K
            den = sum_c1_2 + sum_c2_2 + K

            ssim_map = np.where(den > 0, num / den, 1.0)
            subband_similarities.append(np.mean(ssim_map))

        return float(np.mean(subband_similarities))

    cw_ssim_val = compute_cw_ssim(img_gt, img_cand)
    # Visual regression error percentage (%)
    error_score = (1.0 - cw_ssim_val) * 100.0

    # --- mPE (mean Perturbation Effect) calculation ---
    def compute_mpe(im_gt: np.ndarray, im_cand: np.ndarray, base_ssim: float) -> float:
        drops = []
        # 1. Gaussian blur perturbation (severity: kernel size 3 and 5)
        for ksize in [3, 5]:
            blurred = cv2.GaussianBlur(im_cand, (ksize, ksize), 0)
            ssim_blur = compute_cw_ssim(im_gt, blurred)
            drops.append(max(0.0, base_ssim - ssim_blur))
        # 2. Translation perturbation (severity: shift by 1 and 2 pixels)
        for shift in [1, 2]:
            matrix = np.float32([[1, 0, shift], [0, 1, shift]])
            shifted = cv2.warpAffine(im_cand, matrix, (im_cand.shape[1], im_cand.shape[0]), borderMode=cv2.BORDER_REPLICATE)
            ssim_shift = compute_cw_ssim(im_gt, shifted)
            drops.append(max(0.0, base_ssim - ssim_shift))
        # 3. Pixel noise perturbation (severity: sigma 5 and 10)
        for noise_sigma in [5, 10]:
            noise = np.random.normal(0, noise_sigma, im_cand.shape).astype(np.int16)
            noisy = np.clip(im_cand.astype(np.int16) + noise, 0, 255).astype(np.uint8)
            ssim_noise = compute_cw_ssim(im_gt, noisy)
            drops.append(max(0.0, base_ssim - ssim_noise))
        return float(np.mean(drops)) if drops else 0.0

    mpe_val = compute_mpe(img_gt, img_cand, cw_ssim_val)
    print(f"[Telemetry metrics] CW-SSIM Similarity: {cw_ssim_val:.4f}, Layout mPE Index: {mpe_val:.4f}")

    # --- Robust difference masking & red highlighting ---
    diff = cv2.absdiff(img_gt, img_cand)
    _, thresh = cv2.threshold(diff, 15, 255, cv2.THRESH_BINARY)

    # Filter out isolated sub-pixel anti-aliasing noise using morphological opening
    kernel_open = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    thresh_clean = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel_open)

    color_cand = cv2.imread(str(candidate_path))
    if color_cand.shape[:2] != img_gt.shape[:2]:
        color_cand = cv2.resize(color_cand, (img_gt.shape[1], img_gt.shape[0]))

    # Paint macro-level layout anomalies in BGR Red
    color_cand[thresh_clean == 255] = [0, 0, 255]
    cv2.imwrite(str(diff_output_path), color_cand)

    return error_score


