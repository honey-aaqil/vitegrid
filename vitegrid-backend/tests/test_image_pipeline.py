"""Tests for the three-step image-to-document pipeline.

Mocks the Gemma 4 Vision call so it runs offline. Verifies:
  * import_from_image accepts PNG/JPG/JPEG/WebP and detects the MIME correctly.
  * Per-element style and spacing survive the round trip (no global collapse).
  * Bbox positions are preserved; auto_layout does not overwrite them.
  * Missing file raises FileNotFoundError before any LLM call.
  * Three-step prompt is sent to the vision model.

Run: `.venv/Scripts/python.exe tests/test_image_pipeline.py` from vitegrid-backend/.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

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


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


def _layout_json(blocks: list[dict[str, Any]]) -> str:
    return json.dumps({
        "title": "Sample",
        "page_width_px": 816.0,
        "page_height_px": 1056.0,
        "margin_px": {"top": 72.0, "right": 72.0, "bottom": 72.0, "left": 72.0},
        "blocks": blocks,
    })


def _approved_audit_json() -> str:
    return json.dumps({
        "approved": True,
        "missing_text": [],
        "layout_issues": [],
        "patch_instructions": None,
    })


def install_mock(layout_text: str) -> dict[str, list[Any]]:
    """Replace `_call_with_retry` so the vision + audit calls do not hit the LLM.

    Returns a `state` dict containing every set of contents the production code
    sent to `_call_with_retry`, so tests can assert what was prompted.
    """
    state: dict[str, list[Any]] = {"calls": []}
    audit_text = _approved_audit_json()

    def fake(client: Any, **kwargs: Any) -> _FakeResponse:
        state["calls"].append(kwargs)
        contents = kwargs.get("contents", [])
        first = contents[0] if contents else ""
        # Distinguish vision call from auditor call by the prompt text.
        if isinstance(first, str) and "DocumentLayout" in first and "THREE-STEP MANDATE" in first:
            return _FakeResponse(layout_text)
        return _FakeResponse(audit_text)

    agent._call_with_retry = fake  # type: ignore[assignment]
    return state


def make_image(suffix: str) -> Path:
    fd, name = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    path = Path(name)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)  # arbitrary non-empty bytes
    return path


def case_missing_file() -> None:
    print("\n=== import_from_image: missing file raises before LLM call ===")
    state = install_mock(_layout_json([]))
    try:
        agent.import_from_image("/nonexistent/path/to/image.png")
        expect("raised FileNotFoundError", False, "no exception")
    except FileNotFoundError:
        expect("raised FileNotFoundError", True)
    expect("no LLM call was made for a missing file", state["calls"] == [])


def case_per_element_style_preserved() -> None:
    print("\n=== per-element style and spacing survive the pipeline ===")
    blocks = [
        {
            "id": "block-0",
            "type": "heading",
            "text": "Quarterly Report",
            "bbox": {"x_px": 72.0, "y_px": 72.0, "width_px": 672.0, "height_px": 36.0},
            "style": {
                "font_family": "Cambria",
                "font_size_pt": 22.0,
                "font_weight": "bold",
                "color_hex": "1f3864",
                "background_hex": None,
                "align": "center",
                "border_visible": False,
                "cell_padding_dxa": {"top": 120, "bottom": 120, "left": 180, "right": 180},
                "list_format": "bullet",
                "list_level": 0,
            },
            "spacing": {
                "before_dxa": 0,
                "after_dxa": 240,
                "line_spacing_dxa": 540,
                "line_rule": "exact",
            },
        },
        {
            "id": "block-1",
            "type": "paragraph",
            "text": "Body paragraph in Garamond 10pt.",
            "bbox": {"x_px": 72.0, "y_px": 130.0, "width_px": 672.0, "height_px": 18.0},
            "style": {
                "font_family": "Garamond",
                "font_size_pt": 10.0,
                "font_weight": "normal",
                "color_hex": "1a1a1a",
                "background_hex": None,
                "align": "justify",
                "border_visible": False,
                "cell_padding_dxa": {"top": 120, "bottom": 120, "left": 180, "right": 180},
                "list_format": "bullet",
                "list_level": 0,
            },
            "spacing": {
                "before_dxa": 0,
                "after_dxa": 120,
                "line_spacing_dxa": 276,
                "line_rule": "exact",
            },
        },
    ]
    install_mock(_layout_json(blocks))
    img = make_image(".png")
    try:
        layout, report = agent.import_from_image(img)
    finally:
        img.unlink(missing_ok=True)

    expect("layout has two blocks", len(layout.blocks) == 2, info=str(len(layout.blocks)))
    h = layout.blocks[0]
    p = layout.blocks[1]
    expect("heading retains Cambria 22pt bold", h.style.font_family == "Cambria" and h.style.font_size_pt == 22.0 and h.style.font_weight == "bold")
    expect("paragraph retains Garamond 10pt normal", p.style.font_family == "Garamond" and p.style.font_size_pt == 10.0 and p.style.font_weight == "normal")
    expect("heading and paragraph have DIFFERENT styles (no global collapse)", h.style.font_family != p.style.font_family)
    expect("heading spacing line_spacing_dxa = 540", h.spacing.line_spacing_dxa == 540)
    expect("paragraph spacing line_spacing_dxa = 276 (per-element)", p.spacing.line_spacing_dxa == 276)
    expect("heading bbox preserved", h.bbox is not None and h.bbox.x_px == 72.0 and h.bbox.y_px == 72.0)
    expect("paragraph bbox preserved", p.bbox is not None and p.bbox.y_px == 130.0)
    expect("audit ran (combined LLM + programmatic)", report is not None)


def case_mime_detection() -> None:
    print("\n=== MIME type chosen from file suffix ===")
    for suffix, expected_mime in [(".png", "image/png"), (".jpg", "image/jpeg"), (".jpeg", "image/jpeg"), (".webp", "image/webp")]:
        state = install_mock(_layout_json([
            {
                "id": "block-0", "type": "paragraph", "text": "x",
                "bbox": {"x_px": 0, "y_px": 0, "width_px": 100, "height_px": 20},
                "style": {}, "spacing": {},
            }
        ]))
        img = make_image(suffix)
        try:
            agent.import_from_image(img)
        finally:
            img.unlink(missing_ok=True)
        vision_call = state["calls"][0]
        parts = vision_call.get("contents", [])
        image_part = parts[1] if len(parts) > 1 else None
        observed_mime = getattr(image_part, "mime_type", None) or (
            image_part.inline_data.mime_type if hasattr(image_part, "inline_data") else None
        )
        expect(f"{suffix} -> {expected_mime}", observed_mime == expected_mime, info=f"got {observed_mime}")


def case_prompt_contains_three_steps() -> None:
    print("\n=== vision prompt enforces all three steps ===")
    state = install_mock(_layout_json([
        {
            "id": "block-0", "type": "paragraph", "text": "x",
            "bbox": {"x_px": 0, "y_px": 0, "width_px": 100, "height_px": 20},
            "style": {}, "spacing": {},
        }
    ]))
    img = make_image(".png")
    try:
        agent.import_from_image(img)
    finally:
        img.unlink(missing_ok=True)
    prompt = state["calls"][0]["contents"][0]
    expect("prompt mentions STEP 1 SPATIAL ANCHORING", "STEP 1" in prompt and "SPATIAL ANCHORING" in prompt)
    expect("prompt mentions STEP 2 OPTICAL PARSING", "STEP 2" in prompt and "OPTICAL PARSING" in prompt)
    expect("prompt mentions STEP 3 STYLE MAPPING", "STEP 3" in prompt and "STYLE MAPPING" in prompt)
    expect("prompt enforces PANOSE WeightRat rule", "WeightRat" in prompt and "7.5" in prompt)
    expect("prompt forbids global style collapse", "do NOT" in prompt.lower() or "do not collapse" in prompt.lower())


def case_partial_block_backfilled_by_defaults() -> None:
    print("\n=== blocks with omitted fields get blueprint defaults ===")
    install_mock(_layout_json([
        {
            "id": "block-0",
            "type": "paragraph",
            "text": "Partial",
            "bbox": {"x_px": 72, "y_px": 72, "width_px": 600, "height_px": 24},
            # style / spacing entirely omitted -> Pydantic backfills.
        }
    ]))
    img = make_image(".png")
    try:
        layout, _ = agent.import_from_image(img)
    finally:
        img.unlink(missing_ok=True)
    b = layout.blocks[0]
    expect("font_family default Arial", b.style.font_family == "Arial")
    expect("font_size_pt default 11.0", b.style.font_size_pt == 11.0)
    expect("spacing line_spacing_dxa default 240", b.spacing.line_spacing_dxa == 240)
    expect("spacing line_rule default auto", b.spacing.line_rule == "auto")


# Preserve the real `_call_with_retry` so other test files run cleanly.
_REAL_CALL = agent._call_with_retry  # type: ignore[attr-defined]


if __name__ == "__main__":
    try:
        case_missing_file()
        case_per_element_style_preserved()
        case_mime_detection()
        case_prompt_contains_three_steps()
        case_partial_block_backfilled_by_defaults()
    finally:
        agent._call_with_retry = _REAL_CALL  # type: ignore[assignment]
    print()
    if _failures:
        print(f"FAIL: {len(_failures)} failures")
        for f in _failures:
            print(f"  - {f}")
        sys.exit(1)
    print("PASS: all image-pipeline checks green")
