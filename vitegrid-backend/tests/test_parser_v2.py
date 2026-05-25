"""Synthetic-span tests for parser v2. No PDF I/O.

Verifies:
  * `detect_columns` finds gutters on a two-column layout.
  * v2 separates side-by-side columns that v1 flattens.
  * `_detect_table_v2` catches unbordered tables that v1 misses.
  * The dispatcher honors `VITEGRID_PARSER`.

Run: `.venv/Scripts/python.exe tests/test_parser_v2.py` from vitegrid-backend/.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import parser as docparser
from parser import (
    PageLayout,
    PdfExtraction,
    TextSpan,
    _classify_pdf_layout_v1,
    _classify_pdf_layout_v2,
    _detect_table_v2,
    classify_pdf_layout,
    detect_columns,
)

_failures: list[str] = []


def expect(label: str, cond: bool, info: str = "") -> None:
    symbol = "PASS" if cond else "FAIL"
    print(f"  {symbol}  {label}{f' -- {info}' if info else ''}")
    if not cond:
        _failures.append(label)


def span(text: str, x: float, y: float, *, size: float = 11.0, bold: bool = False) -> TextSpan:
    # Each char ~ 6pt wide at 11pt body, ~ row height = size.
    w = max(1.0, len(text) * (size * 0.55))
    return TextSpan(
        text=text,
        bbox=(x, y, x + w, y + size),
        font="Arial",
        size_pt=size,
        color_hex="#000000",
        bold=bold,
        italic=False,
        page_index=0,
    )


def page(spans: list[TextSpan], *, w: float = 612.0, h: float = 792.0) -> PageLayout:
    return PageLayout(page_index=0, page_width_pt=w, page_height_pt=h, spans=spans, image_bytes=b"")


def extraction(spans: list[TextSpan]) -> PdfExtraction:
    return PdfExtraction(pages=[page(spans)], is_scanned=False)


def case_detect_columns_two_column() -> None:
    print("\n=== detect_columns: two-column resume layout ===")
    spans = [
        # Left column anchored at x=60. Text widths kept under ~135pt so the
        # column ends well before the right column starts at x=340.
        span("Profile", 60, 80, size=14, bold=True),
        span("Engineer for 5 years.", 60, 100),
        span("Skills", 60, 140, size=14, bold=True),
        span("Python Ruby TS Rust", 60, 160),
        # Right column at x=340. Same width discipline.
        span("Experience", 340, 80, size=14, bold=True),
        span("Worked at Acme Inc.", 340, 100),
        span("Education", 340, 140, size=14, bold=True),
        span("MIT in Cambridge MA", 340, 160),
    ]
    cols = detect_columns(spans, page_width_pt=612.0)
    expect("detected two columns", len(cols) == 2, info=str(cols))
    if len(cols) == 2:
        l, r = cols
        expect("left column starts near 60pt", abs(l[0] - 60.0) < 5.0, info=str(l))
        expect("right column starts near 340pt", abs(r[0] - 340.0) < 5.0, info=str(r))


def case_detect_columns_single() -> None:
    print("\n=== detect_columns: dense single-column body ===")
    spans = [
        span("Paragraph one continues across the full content width " + ("x " * 40), 72, y)
        for y in (80, 100, 120, 140)
    ]
    cols = detect_columns(spans, page_width_pt=612.0)
    expect("single column", len(cols) == 1, info=str(cols))


def case_v1_vs_v2_columns() -> None:
    print("\n=== v1 flattens columns, v2 keeps them apart ===")
    spans = [
        # Two distinct columns sharing Y baselines. v1 will merge them on the y
        # axis; v2 should keep them separate via projection-based gutters.
        span("Profile section info", 60, 100),
        span("Experience details", 340, 100),
        span("Skills and tools", 60, 120),
        span("Education path here", 340, 120),
    ]
    ex = extraction(spans)
    v1_blocks = _classify_pdf_layout_v1(ex)
    v2_blocks = _classify_pdf_layout_v2(ex)

    def block_payload(b):
        if b.text:
            return b.text
        if b.rows:
            return " | ".join(" ".join(r) for r in b.rows)
        if b.items:
            return " ".join(b.items)
        return ""

    v1_payloads = [block_payload(b) for b in v1_blocks]
    v2_payloads = [block_payload(b) for b in v2_blocks]
    # v1 mixes both columns into one block (either as flattened paragraph text
    # or as a fake table where columns are inferred from whitespace gaps).
    expect(
        "v1 conflates both columns into a single block (flattening bug)",
        any("Profile" in p and "Experience" in p for p in v1_payloads),
        info=str(v1_payloads),
    )
    # v2 separates blocks by column.
    left_only = any("Profile" in p and "Experience" not in p for p in v2_payloads)
    right_only = any("Experience" in p and "Profile" not in p for p in v2_payloads)
    expect("v2 emits a left-only block", left_only, info=str(v2_payloads))
    expect("v2 emits a right-only block", right_only, info=str(v2_payloads))


def case_detect_table_v2_unbordered() -> None:
    print("\n=== _detect_table_v2: cross-row anchor alignment on unbordered tables ===")
    # Three rows, two anchored columns at x=60 and x=240.
    rows = [
        [span("Year", 60, 100, bold=True), span("Role", 240, 100, bold=True)],
        [span("2022", 60, 116), span("Senior Engineer", 240, 116)],
        [span("2023", 60, 132), span("Tech Lead", 240, 132)],
    ]
    result = _detect_table_v2(rows)
    expect("detected as table", result is not None, info=str(result))
    if result is not None:
        expect("three rows extracted", len(result) == 3, info=str(result))
        expect("first row header preserved", "Year" in result[0][0] and "Role" in result[0][1])


def case_detect_table_v2_rejects_prose() -> None:
    print("\n=== _detect_table_v2: rejects prose paragraphs ===")
    rows = [
        [span("This is a normal paragraph line.", 60, 100)],
        [span("Continued on the next line.", 60, 116)],
        [span("And a third one.", 60, 132)],
    ]
    expect("prose not flagged as table", _detect_table_v2(rows) is None)


def case_dispatcher_env() -> None:
    print("\n=== classify_pdf_layout: VITEGRID_PARSER dispatcher ===")
    spans = [span("Hi", 60, 100, size=18, bold=True), span("Body.", 60, 130)]
    ex = extraction(spans)
    saved = os.environ.get("VITEGRID_PARSER")
    try:
        os.environ.pop("VITEGRID_PARSER", None)
        default_blocks = classify_pdf_layout(ex)
        os.environ["VITEGRID_PARSER"] = "v2"
        v2_blocks = classify_pdf_layout(ex)
        expect("default returns v1 results", len(default_blocks) > 0)
        expect("v2 returns at least as many blocks", len(v2_blocks) >= len(default_blocks))
    finally:
        if saved is None:
            os.environ.pop("VITEGRID_PARSER", None)
        else:
            os.environ["VITEGRID_PARSER"] = saved


if __name__ == "__main__":
    case_detect_columns_two_column()
    case_detect_columns_single()
    case_v1_vs_v2_columns()
    case_detect_table_v2_unbordered()
    case_detect_table_v2_rejects_prose()
    case_dispatcher_env()
    print()
    if _failures:
        print(f"FAIL: {len(_failures)} failures")
        for f in _failures:
            print(f"  - {f}")
        sys.exit(1)
    print("PASS: all parser-v2 checks green")
