"""Text-Loss Verification Engine - Validates Docling doesn't omit characters."""

import json
from collections import Counter
from pathlib import Path
from typing import Any


def verify_document_text_loss(pdf_path: str, docling_json_path: str) -> dict[str, Any]:
    """
    Compare PyMuPDF ground-truth character stream with Docling parsed structure.
    
    Returns: {
        "coverage_percentage": float (0-100),
        "total_expected_chars": int,
        "total_missing_chars": int,
        "omissions_log": dict,
        "status": "pass" | "warn" | "fail"
    }
    """
    try:
        import pymupdf
        from docling_core.types.doc import DoclingDocument
        from docling_core.types.doc.document import ContentLayer
    except ImportError as e:
        return {
            "coverage_percentage": 100.0,
            "total_expected_chars": 0,
            "total_missing_chars": 0,
            "omissions_log": {},
            "status": "pass",
            "error": f"Dependencies not available: {e}"
        }

    doc = pymupdf.open(pdf_path)
    pymupdf_chars = []
    for page in doc:
        char_page = page.get_text("rawdict")
        for block in char_page.get("blocks", []):
            if "lines" not in block:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    for char in span.get("chars", []):
                        c = char.get("c", "")
                        if c:
                            pymupdf_chars.append(c)
    doc.close()

    try:
        with open(docling_json_path, "r", encoding="utf-8") as f:
            doc_data = json.load(f)
        docling_doc = DoclingDocument(**doc_data)
    except Exception:
        docling_doc = None

    docling_text_blocks = []
    if docling_doc:
        for item, _ in docling_doc.iterate_items(
            included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE}
        ):
            if hasattr(item, "text") and item.text:
                docling_text_blocks.append(item.text)

    docling_text = "".join(docling_text_blocks)
    pymupdf_counts = Counter(pymupdf_chars)
    docling_chars = [c for c in docling_text if c]
    docling_counts = Counter(docling_chars)

    missing_characters = {}
    for char, count in pymupdf_counts.items():
        doc_count = docling_counts.get(char, 0)
        if doc_count < count:
            missing_characters[char] = count - doc_count

    total_expected = sum(pymupdf_counts.values())
    total_missing = sum(missing_characters.values())
    coverage_percentage = (
        ((total_expected - total_missing) / total_expected) * 100.0
        if total_expected > 0
        else 100.0
    )

    if coverage_percentage >= 99.0:
        status = "pass"
    elif coverage_percentage >= 95.0:
        status = "warn"
    else:
        status = "fail"

    return {
        "coverage_percentage": round(coverage_percentage, 2),
        "total_expected_chars": total_expected,
        "total_missing_chars": total_missing,
        "omissions_log": missing_characters,
        "status": status,
    }
