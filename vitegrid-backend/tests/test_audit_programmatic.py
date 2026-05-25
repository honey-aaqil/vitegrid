"""Regression suite for `audit_layout_programmatic`. No Gemma calls.

Run: `.venv/Scripts/python.exe tests/test_audit_programmatic.py` from vitegrid-backend/.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Keep the module import side-effect free even when env vars are unset.
os.environ.setdefault("GEMMA_API_KEY_CORE", "test")
os.environ.setdefault("GEMMA_API_KEY_AUDIT", "test")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent
from agent import (
    AuditReport,
    BlockType,
    BoundingBox,
    DocumentBlock,
    DocumentLayout,
    StyleTokens,
    audit_layout_programmatic,
    wcag_contrast,
)


_failures: list[str] = []


def expect(label: str, cond: bool, info: str = "") -> None:
    symbol = "PASS" if cond else "FAIL"
    print(f"  {symbol}  {label}{f' -- {info}' if info else ''}")
    if not cond:
        _failures.append(label)


def make_layout(blocks: list[DocumentBlock], *, w: int = 816, h: int = 1056) -> DocumentLayout:
    return DocumentLayout(
        title="t",
        page_width_px=w,
        page_height_px=h,
        margin_px={"top": 72, "right": 72, "bottom": 72, "left": 72},
        blocks=blocks,
    )


def case_wcag() -> None:
    print("\n=== wcag_contrast helper ===")
    expect("black-on-white ~= 21:1", abs((wcag_contrast("#000000", "#FFFFFF") or 0) - 21.0) < 0.01)
    expect("white-on-white = 1:1", abs((wcag_contrast("#FFFFFF", "#FFFFFF") or 0) - 1.0) < 0.001)
    expect(
        "low-contrast grays well below threshold",
        (wcag_contrast("#777777", "#888888") or 99) < 1.5,
    )
    expect("None for unparseable hex", wcag_contrast("not-a-color", "#FFFFFF") is None)
    expect("3-digit hex expands", abs((wcag_contrast("#000", "#FFF") or 0) - 21.0) < 0.01)


def case_clean_layout() -> None:
    print("\n=== a clean generated layout ===")
    layout = make_layout([
        DocumentBlock(
            id="block-0",
            type=BlockType.HEADING,
            text="Title",
            bbox=BoundingBox(x_px=72, y_px=72, width_px=672, height_px=40),
            style=StyleTokens(font_size_pt=18.0, color_hex="#000000", background_hex="#FFFFFF"),
        ),
        DocumentBlock(
            id="block-1",
            type=BlockType.PARAGRAPH,
            text="Body content.",
            bbox=BoundingBox(x_px=72, y_px=140, width_px=672, height_px=80),
            style=StyleTokens(font_size_pt=11.0, color_hex="#1a1a1a", background_hex="#FFFFFF"),
        ),
    ])
    r = audit_layout_programmatic(layout)
    expect("approved", r.approved)
    expect("no issues", r.layout_issues == [], info=str(r.layout_issues))
    expect("no missing text", r.missing_text == [])


def case_overlap() -> None:
    print("\n=== overlapping blocks (no nesting) ===")
    layout = make_layout([
        DocumentBlock(
            id="A",
            type=BlockType.PARAGRAPH,
            text="A",
            bbox=BoundingBox(x_px=72, y_px=72, width_px=300, height_px=80),
            style=StyleTokens(font_size_pt=11.0),
        ),
        DocumentBlock(
            id="B",
            type=BlockType.PARAGRAPH,
            text="B",
            bbox=BoundingBox(x_px=150, y_px=100, width_px=300, height_px=80),
            style=StyleTokens(font_size_pt=11.0),
        ),
    ])
    r = audit_layout_programmatic(layout)
    expect("not approved", not r.approved)
    expect(
        "issue mentions IoU",
        any("IoU" in i for i in r.layout_issues),
        info=str(r.layout_issues),
    )


def case_nesting_is_allowed() -> None:
    print("\n=== nested blocks (child fully inside parent) are NOT flagged ===")
    layout = make_layout([
        DocumentBlock(
            id="outer",
            type=BlockType.TABLE,
            rows=[["x"]],
            bbox=BoundingBox(x_px=72, y_px=72, width_px=600, height_px=400),
            style=StyleTokens(font_size_pt=11.0),
        ),
        DocumentBlock(
            id="inner",
            type=BlockType.PARAGRAPH,
            text="inside",
            bbox=BoundingBox(x_px=100, y_px=100, width_px=200, height_px=40),
            style=StyleTokens(font_size_pt=11.0),
        ),
    ])
    r = audit_layout_programmatic(layout)
    expect("nested layout approved", r.approved, info=str(r.layout_issues))


def case_low_contrast() -> None:
    print("\n=== low contrast text ===")
    layout = make_layout([
        DocumentBlock(
            id="dim",
            type=BlockType.PARAGRAPH,
            text="hard to read",
            bbox=BoundingBox(x_px=72, y_px=72, width_px=300, height_px=40),
            style=StyleTokens(font_size_pt=11.0, color_hex="#888888", background_hex="#999999"),
        ),
    ])
    r = audit_layout_programmatic(layout)
    expect("not approved", not r.approved)
    expect("contrast issue surfaced", any("contrast" in i for i in r.layout_issues))


def case_font_bounds() -> None:
    print("\n=== font size out of bounds ===")
    layout = make_layout([
        DocumentBlock(
            id="tiny",
            type=BlockType.PARAGRAPH,
            text="microprint",
            style=StyleTokens(font_size_pt=2.0),
        ),
        DocumentBlock(
            id="huge",
            type=BlockType.HEADING,
            text="billboard",
            style=StyleTokens(font_size_pt=200.0),
        ),
    ])
    r = audit_layout_programmatic(layout)
    expect("two font issues", sum(1 for i in r.layout_issues if "font" in i) == 2, info=str(r.layout_issues))


def case_missing_text() -> None:
    print("\n=== missing source text run ===")
    layout = make_layout([
        DocumentBlock(
            id="block-0",
            type=BlockType.PARAGRAPH,
            text="We kept this line.",
            style=StyleTokens(font_size_pt=11.0),
        ),
    ])
    r = audit_layout_programmatic(
        layout,
        source_text_runs=["We kept this line.", "But dropped this one."],
    )
    expect("not approved", not r.approved)
    expect("missing text reported", "But dropped this one." in r.missing_text)
    expect("kept text not reported", "We kept this line." not in r.missing_text)


def case_off_page() -> None:
    print("\n=== block off-page bounds ===")
    layout = make_layout([
        DocumentBlock(
            id="overflow",
            type=BlockType.PARAGRAPH,
            text="x",
            bbox=BoundingBox(x_px=72, y_px=72, width_px=2000, height_px=80),
            style=StyleTokens(font_size_pt=11.0),
        ),
    ])
    r = audit_layout_programmatic(layout)
    expect("not approved", not r.approved)
    expect("right-edge issue surfaced", any("right edge" in i for i in r.layout_issues))


def case_merge_reports() -> None:
    print("\n=== _merge_reports ===")
    a = AuditReport(
        approved=False,
        missing_text=["a"],
        layout_issues=["IoU x"],
        patch_instructions="fix x",
    )
    b = AuditReport(
        approved=True,
        missing_text=["b", "a"],
        layout_issues=["font y"],
        patch_instructions=None,
    )
    merged = agent._merge_reports(a, b)
    expect("approved = AND", merged.approved is False)
    expect(
        "missing_text union, dedup, order preserved",
        merged.missing_text == ["a", "b"],
        info=str(merged.missing_text),
    )
    expect(
        "layout_issues union",
        set(merged.layout_issues) == {"IoU x", "font y"},
        info=str(merged.layout_issues),
    )
    expect("patch_instructions = first non-null", merged.patch_instructions == "fix x")


def case_none_coercion_to_defaults() -> None:
    """Guard against the 2026-05-25 prod regression: import_from_classified_blocks
    passed border_visible=None into StyleTokens, which Pydantic rejected after the
    blueprint alignment made fields mandatory. The model_validator now coerces
    None to defaults for non-Optional fields and preserves None for Optional ones.
    """
    print("\n=== None-coercion to blueprint defaults ===")
    # Exact failure case from the production traceback:
    s = StyleTokens(
        font_family=None,
        font_size_pt=None,
        font_weight="normal",
        color_hex=None,
        align="left",
        border_visible=None,
    )
    expect("border_visible None -> default True", s.border_visible is True)
    expect("font_family None -> default Arial", s.font_family == "Arial")
    expect("font_size_pt None -> default 11.0", s.font_size_pt == 11.0)
    expect("color_hex None -> default '000000'", s.color_hex == "000000")
    # background_hex is the one field with `str | None` — None is preserved.
    s_transparent = StyleTokens(background_hex=None)
    expect("background_hex None preserved (Optional)", s_transparent.background_hex is None)
    # Same coercion semantics for SpacingTokens.
    sp = agent.SpacingTokens(before_dxa=None, after_dxa=None, line_spacing_dxa=None, line_rule=None)
    expect(
        "SpacingTokens all-None coerces to defaults",
        sp.before_dxa == 0
        and sp.after_dxa == 0
        and sp.line_spacing_dxa == 240
        and sp.line_rule == "auto",
        info=str(sp.model_dump()),
    )


def case_markdown_text_runs() -> None:
    print("\n=== _markdown_text_runs ===")
    runs = agent._markdown_text_runs(
        "# Heading\n\n- first bullet\n- second bullet\n\n| col1 | col2 |\n|------|------|\n| a    | b    |\n"
    )
    expect("heading line stripped of #", "Heading" in runs)
    expect("bullet content present", "first bullet" in runs)
    expect("separator row filtered", not any(set(r) <= set("|-: ") for r in runs))


if __name__ == "__main__":
    case_wcag()
    case_clean_layout()
    case_overlap()
    case_nesting_is_allowed()
    case_low_contrast()
    case_font_bounds()
    case_missing_text()
    case_off_page()
    case_merge_reports()
    case_none_coercion_to_defaults()
    case_markdown_text_runs()
    print()
    if _failures:
        print(f"FAIL: {len(_failures)} failures")
        for f in _failures:
            print(f"  - {f}")
        sys.exit(1)
    print("PASS: all audit checks green")
