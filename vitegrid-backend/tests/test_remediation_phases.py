"""Regression tests for the four remediation patches.

  Patch 1.1/1.2/1.3 — prompt constraints forbid hallucinating tables for
                       multi-column non-tabular text (resume skill matrices,
                       key-value pairs). Confirmed by prompt-content asserts.
  Patch 2          — _assemble_text_from_spans merges intra-word fragments
                       when horizontal gap < font_size * 0.25.

Run: `.venv/Scripts/python.exe tests/test_remediation_phases.py` from
vitegrid-backend/.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("GEMMA_API_KEY_CORE", "test")
os.environ.setdefault("GEMMA_API_KEY_AUDIT", "test")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent

_failures: list[str] = []


def expect(label: str, cond: bool, info: str = "") -> None:
    symbol = "PASS" if cond else "FAIL"
    print(f"  {symbol}  {label}{f' -- {info}' if info else ''}")
    if not cond:
        _failures.append(label)


@dataclass
class _FakeSpan:
    text: str
    bbox: tuple[float, float, float, float]
    size_pt: float = 11.0
    font: str = "Arial"
    color_hex: str = "000000"
    bold: bool = False
    italic: bool = False


def case_prompt_topology_constraints() -> None:
    print("\n=== Patch 1.x: prompts forbid hallucinating tables ===")
    expect(
        "_AGENT5_PROMPT has STRICT TABLE TOPOLOGY RULE",
        "STRICT TABLE TOPOLOGY RULE" in agent._AGENT5_PROMPT,
    )
    expect(
        "_AGENT5_PROMPT forbids table for multi-column non-tabular text",
        "multi-column layout text" in agent._AGENT5_PROMPT
        and "DO NOT use" in agent._AGENT5_PROMPT,
    )
    expect(
        "_AGENT_IMAGE_PIPELINE_PROMPT has CRITICAL TOPOLOGICAL CONSTRAINT",
        "CRITICAL TOPOLOGICAL CONSTRAINT" in agent._AGENT_IMAGE_PIPELINE_PROMPT,
    )
    expect(
        "_AGENT_IMAGE_PIPELINE_PROMPT requires visible gridlines for tables",
        "visible gridlines" in agent._AGENT_IMAGE_PIPELINE_PROMPT,
    )
    expect(
        "_AGENT_REVIEW_PROMPT scopes table to visible structural lines",
        "visible structural lines" in agent._AGENT_REVIEW_PROMPT,
    )
    expect(
        "_AGENT_REVIEW_PROMPT explicitly forbids paragraph->table override",
        "Do NOT override a paragraph or list back to table" in agent._AGENT_REVIEW_PROMPT,
    )


def case_intra_word_merge_basic() -> None:
    """The TJ-operator-fragmented 'Java' from PyMuPDF arrives as two adjacent
    spans with bboxes that touch or slightly overlap. The new assembler must
    concatenate without inserting a space."""
    print("\n=== Patch 2: fragmented 'Java' merges into one token ===")
    # 'Jav' at x=[100, 121] and 'a' at x=[121, 128] on the same baseline.
    spans = [
        _FakeSpan(text="Jav", bbox=(100.0, 50.0, 121.0, 62.0), size_pt=11.0),
        _FakeSpan(text="a",   bbox=(121.0, 50.0, 128.0, 62.0), size_pt=11.0),
    ]
    result = agent._assemble_text_from_spans([0, 1], spans)
    expect("'Jav' + 'a' merges to 'Java'", result == "Java", info=repr(result))


def case_intra_word_merge_with_kerning_overlap() -> None:
    """Kerning can cause negative x_gap (bboxes overlap). Still intra-word."""
    print("\n=== Patch 2: negative-gap kerning still merges ===")
    spans = [
        _FakeSpan(text="Pyt", bbox=(100.0, 50.0, 122.0, 62.0), size_pt=11.0),
        # Next span starts at x=121 — that's 1pt before previous end (overlap).
        _FakeSpan(text="hon", bbox=(121.0, 50.0, 143.0, 62.0), size_pt=11.0),
    ]
    result = agent._assemble_text_from_spans([0, 1], spans)
    expect("overlapping bboxes still merge to 'Python'", result == "Python", info=repr(result))


def case_word_boundary_preserved() -> None:
    """Two genuine words separated by a real space gap (>= 0.25 * font_size)
    must keep the space."""
    print("\n=== Patch 2: real word boundary keeps the space ===")
    # 'Hello' at x=[100, 130], then 'World' at x=[150, 180]: gap = 20pt,
    # which is well above 11pt * 0.25 = 2.75pt.
    spans = [
        _FakeSpan(text="Hello", bbox=(100.0, 50.0, 130.0, 62.0), size_pt=11.0),
        _FakeSpan(text="World", bbox=(150.0, 50.0, 180.0, 62.0), size_pt=11.0),
    ]
    result = agent._assemble_text_from_spans([0, 1], spans)
    expect("real space gap keeps the space", result == "Hello World", info=repr(result))


def case_line_breaks_preserved() -> None:
    """Spans on different baselines should produce a newline between them."""
    print("\n=== Patch 2: line breaks are preserved across baselines ===")
    spans = [
        _FakeSpan(text="Line one", bbox=(100.0, 50.0, 150.0, 62.0), size_pt=11.0),
        _FakeSpan(text="Line two", bbox=(100.0, 80.0, 150.0, 92.0), size_pt=11.0),
    ]
    result = agent._assemble_text_from_spans([0, 1], spans)
    expect("baseline change becomes newline", result == "Line one\nLine two", info=repr(result))


def case_resume_skill_row_no_fragmentation() -> None:
    """Realistic resume skill row: 'Java Python Rust' arrives as 6 spans
    where each word may be broken into 2 pieces by font subsetting.
    Result must be exactly 'Java Python Rust'."""
    print("\n=== Patch 2: realistic resume skill row reassembles cleanly ===")
    spans = [
        _FakeSpan(text="Ja",   bbox=(100.0, 50.0, 113.0, 62.0)),
        _FakeSpan(text="va",   bbox=(113.0, 50.0, 126.0, 62.0)),
        _FakeSpan(text="Py",   bbox=(150.0, 50.0, 163.0, 62.0)),
        _FakeSpan(text="thon", bbox=(163.0, 50.0, 188.0, 62.0)),
        _FakeSpan(text="Ru",   bbox=(220.0, 50.0, 233.0, 62.0)),
        _FakeSpan(text="st",   bbox=(233.0, 50.0, 246.0, 62.0)),
    ]
    result = agent._assemble_text_from_spans(list(range(6)), spans)
    expect(
        "six fragments reassemble to 'Java Python Rust'",
        result == "Java Python Rust",
        info=repr(result),
    )


def case_empty_inputs() -> None:
    print("\n=== Patch 2: edge cases (empty inputs) ===")
    expect("empty indices -> empty string", agent._assemble_text_from_spans([], []) == "")
    expect("invalid indices -> empty string", agent._assemble_text_from_spans([5, 6], []) == "")


if __name__ == "__main__":
    case_prompt_topology_constraints()
    case_intra_word_merge_basic()
    case_intra_word_merge_with_kerning_overlap()
    case_word_boundary_preserved()
    case_line_breaks_preserved()
    case_resume_skill_row_no_fragmentation()
    case_empty_inputs()
    print()
    if _failures:
        print(f"FAIL: {len(_failures)} failures")
        for f in _failures:
            print(f"  - {f}")
        sys.exit(1)
    print("PASS: all remediation-phase checks green")
