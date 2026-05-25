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
    """Negative-coordinate bboxes are the only off-page case we still flag.
    Page-edge OVERFLOW on either axis is allowed because Word flow-wraps and
    flow-paginates (see `case_multi_page_horizontal_overflow_is_allowed` and
    `case_multi_page_bbox_is_allowed`)."""
    print("\n=== block off-page: negative origin is a bug ===")
    layout = make_layout([
        DocumentBlock(
            id="neg",
            type=BlockType.PARAGRAPH,
            text="x",
            bbox=BoundingBox(x_px=-10, y_px=-5, width_px=100, height_px=20),
            style=StyleTokens(font_size_pt=11.0),
        ),
    ])
    r = audit_layout_programmatic(layout)
    expect("not approved", not r.approved)
    expect(
        "negative-origin issue surfaced",
        any("starts off-page" in i for i in r.layout_issues),
        info=str(r.layout_issues),
    )


def case_multi_page_horizontal_overflow_is_allowed() -> None:
    """Regression: production audit flagged 13 blocks for 'extends past right
    edge' on a PDF iLovePDF converts without complaint. Word flow-wraps text
    to the section content width, so horizontal bbox overflow is not a real
    layout bug; we mirror the existing vertical-overflow tolerance."""
    print("\n=== horizontal page-edge overflow is approved ===")
    blocks = [
        DocumentBlock(
            id=f"block-{i}",
            type=BlockType.PARAGRAPH,
            text=f"line {i}",
            # x+width = 72 + 800 = 872, well past the 816 page width
            bbox=BoundingBox(x_px=72, y_px=72 + i * 18, width_px=800, height_px=16),
            style=StyleTokens(font_size_pt=11.0, color_hex="000000", background_hex="FFFFFF"),
        )
        for i in range(13)
    ]
    layout = make_layout(blocks)
    r = audit_layout_programmatic(layout)
    right_edge_issues = [i for i in r.layout_issues if "right" in i.lower()]
    expect("no right-edge issues flagged", right_edge_issues == [], info=str(right_edge_issues))
    expect("approved", r.approved, info=str(r.layout_issues))


def case_filter_strips_llm_bottom_edge_messages() -> None:
    """Regression: production audits surfaced both flavors of the off-page-Y
    false positive: the verbose LLM-generated 'Block X y_px + height_px (...)
    exceeds page_height_px (...)' AND the terse programmatic 'block[X] extends
    past bottom edge'. The post-filter must catch both regardless of source.
    """
    print("\n=== false-positive filter strips bottom-edge messages ===")
    # Mirrors the exact strings observed in production at 2026-05-25:
    fake_report = AuditReport(
        approved=False,
        missing_text=[],
        layout_issues=[
            # Verbose LLM-generated flavor
            "Block block-20 y_px + height_px (1058.5) exceeds page_height_px (1056.0)",
            "Block block-21 y_px + height_px (1092.5) exceeds page_height_px (1056.0)",
            # Terse programmatic flavor (older release; filter must still catch it)
            "block[block-22] extends past bottom edge",
            "block[block-23] extends past bottom edge",
            # A REAL issue that should survive the filter
            "block[block-24] contrast 1.20:1 below WCAG-AA threshold 3.0",
        ],
        patch_instructions="...",
    )
    cleaned = agent._strip_bottom_edge_false_positives(fake_report)
    expect(
        "verbose LLM bottom-edge messages dropped",
        not any("exceeds page_height" in i for i in cleaned.layout_issues),
        info=str(cleaned.layout_issues),
    )
    expect(
        "terse programmatic bottom-edge messages dropped",
        not any("extends past bottom" in i for i in cleaned.layout_issues),
        info=str(cleaned.layout_issues),
    )
    expect(
        "real contrast issue survives the filter",
        any("contrast" in i for i in cleaned.layout_issues),
        info=str(cleaned.layout_issues),
    )

    # If the ONLY issues were false positives, the report should be approved.
    only_false_positives = AuditReport(
        approved=False,
        missing_text=[],
        layout_issues=[
            "Block block-20 y_px + height_px (1058.5) exceeds page_height_px (1056.0)",
            "block[block-21] extends past bottom edge",
        ],
        patch_instructions="fix it",
    )
    cleaned2 = agent._strip_bottom_edge_false_positives(only_false_positives)
    expect("report becomes approved when no real issues remain", cleaned2.approved)
    expect("patch_instructions cleared on approval", cleaned2.patch_instructions is None)


def case_multi_page_bbox_is_allowed() -> None:
    """Regression: production audit flagged block-25..block-34 as 'extends past
    bottom edge' for a 35-block import. Word flow-paginates the .docx, so
    vertical overflow is normal for multi-page content. The check was removed.
    """
    print("\n=== multi-page content is approved (no bottom-edge flag) ===")
    # 30 paragraphs at 44px stride, like the production trace; later blocks
    # legitimately have y > page_height_px = 1056.
    blocks = [
        DocumentBlock(
            id=f"block-{i}",
            type=BlockType.PARAGRAPH,
            text=f"line {i}",
            bbox=BoundingBox(x_px=72, y_px=72.0 + i * 44.0, width_px=672, height_px=40),
            style=StyleTokens(font_size_pt=11.0, color_hex="000000", background_hex="FFFFFF"),
        )
        for i in range(30)
    ]
    layout = make_layout(blocks)
    r = audit_layout_programmatic(layout)
    bottom_issues = [i for i in r.layout_issues if "bottom" in i.lower()]
    expect("no bottom-edge issues flagged", bottom_issues == [], info=str(bottom_issues))
    expect("approved", r.approved, info=str(r.layout_issues))


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
    case_multi_page_bbox_is_allowed()
    case_multi_page_horizontal_overflow_is_allowed()
    case_filter_strips_llm_bottom_edge_messages()
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
