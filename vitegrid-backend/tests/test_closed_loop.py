"""Unit tests for the Visual Regression Closed-Loop Optimization pipeline.

Verifies:
  * `calculate_visual_regression` correctly evaluates error rate and highlights differences.
  * `optimize_template_closed_loop` runs iterations and applies block style patches.
  * Loop exits early when error score is below target threshold.

Run: `.venv/Scripts/python.exe tests/test_closed_loop.py` from vitegrid-backend/.
"""
from __future__ import annotations

import copy
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("GEMMA_API_KEY_CORE", "test")
os.environ.setdefault("GEMMA_API_KEY_AUDIT", "test")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np
from pydantic import BaseModel

import agent
import parser as docparser
from agent import (
    BlockStylePatch,
    DocumentBlock,
    DocumentLayout,
    LayoutRefinementPatches,
    StyleTokens,
    BoundingBox,
)

_failures: list[str] = []


def expect(label: str, cond: bool, info: str = "") -> None:
    symbol = "PASS" if cond else "FAIL"
    print(f"  {symbol}  {label}{f' -- {info}' if info else ''}")
    if not cond:
        _failures.append(label)


def create_solid_image(path: Path, color: int, width: int = 100, height: int = 100) -> None:
    # Creates a single channel grayscale image
    img = np.full((height, width), color, dtype=np.uint8)
    cv2.imwrite(str(path), img)


def case_visual_regression_identical() -> None:
    print("\n=== calculate_visual_regression: identical images ===")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        gt_path = tmp_path / "gt.png"
        cand_path = tmp_path / "cand.png"
        diff_path = tmp_path / "diff.png"

        create_solid_image(gt_path, 255)
        create_solid_image(cand_path, 255)

        score = docparser.calculate_visual_regression(gt_path, cand_path, diff_path)
        expect("error score is exactly 0", abs(score) < 1e-5, f"got {score}")
        expect("diff file written", diff_path.exists())


def case_visual_regression_differences() -> None:
    print("\n=== calculate_visual_regression: differing images ===")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        gt_path = tmp_path / "gt.png"
        cand_path = tmp_path / "cand.png"
        diff_path = tmp_path / "diff.png"

        # 100x100 images = 10000 pixels.
        # Let's make a 10x10 area different (100 pixels = 1% difference).
        img_gt = np.full((100, 100), 255, dtype=np.uint8)
        img_cand = np.full((100, 100), 255, dtype=np.uint8)
        img_cand[10:20, 10:20] = 0

        cv2.imwrite(str(gt_path), img_gt)
        cv2.imwrite(str(cand_path), img_cand)

        score = docparser.calculate_visual_regression(gt_path, cand_path, diff_path)
        expect("error score is around 1.0%", abs(score - 1.0) < 0.1, f"got {score}")
        expect("diff file exists", diff_path.exists())

        # Verify diff color tinting
        diff_img = cv2.imread(str(diff_path))
        # Differences should be tinted red (BGR: [0, 0, 255])
        pixel_in_diff = diff_img[15, 15]
        expect("differing pixel tinted red", list(pixel_in_diff) == [0, 0, 255], f"got {pixel_in_diff}")
        # Unchanged pixel should remain white
        pixel_white = diff_img[5, 5]
        expect("unchanged pixel remains white", list(pixel_white) == [255, 255, 255], f"got {pixel_white}")


def case_closed_loop_optimization() -> None:
    print("\n=== optimize_template_closed_loop: dry run / mocking ===")

    # Setup dummy layout
    initial_layout = DocumentLayout(
        title="Test Doc",
        page_width_px=816.0,
        page_height_px=1056.0,
        margin_px={"top": 72.0, "right": 72.0, "bottom": 72.0, "left": 72.0},
        blocks=[
            DocumentBlock(
                id="block-0",
                type="paragraph",
                text="Hello world",
                bbox=BoundingBox(x_px=72.0, y_px=72.0, width_px=100.0, height_px=20.0),
                style=StyleTokens(
                    font_family="Arial",
                    font_size_pt=12.0,
                    font_weight="normal",
                    align="left",
                    color_hex="000000",
                )
            )
        ]
    )

    # Let's mock render_layout_screenshot to avoid launching playwright
    saved_render = docparser.render_layout_screenshot
    saved_regression = docparser.calculate_visual_regression
    saved_refine = agent.agent_refine_layout_schema

    # We will simulate:
    # Iteration 0: error score 10.0%, refinement returns a font size patch + margin patch.
    # Iteration 1: error score 0.2% (below threshold), exits early.
    refine_calls = []
    regression_calls = []

    def mock_render(layout_json_str: str, output_path: Path, width: int = 816, height: int = 1056):
        # Write a dummy image file so calculation has something to read
        create_solid_image(output_path, 255, width=width, height=height)

    def mock_regression(gt_path: Path, cand_path: Path, diff_path: Path) -> float:
        regression_calls.append((gt_path, cand_path, diff_path))
        # Write dummy diff
        create_solid_image(diff_path, 255)
        # First call returns 10.0%, subsequent return 0.2%
        if len(regression_calls) == 1:
            return 10.0
        return 0.2

    def mock_refine(gt_bytes: bytes, cand_bytes: bytes, diff_bytes: bytes, current_layout: DocumentLayout) -> LayoutRefinementPatches:
        refine_calls.append(current_layout)
        return LayoutRefinementPatches(
            diagnostics="Test run refinement",
            patches=[
                BlockStylePatch(
                    block_id="block-0",
                    font_size_change_pt=2.0,
                    align_patch="center",
                    font_weight_patch="bold",
                    margin_top_shift_px=10,
                )
            ]
        )

    docparser.render_layout_screenshot = mock_render
    docparser.calculate_visual_regression = mock_regression
    agent.agent_refine_layout_schema = mock_refine

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            pdf_path = tmp_path / "dummy.pdf"
            
            # Create a mock 1-page PDF using fitz (PyMuPDF)
            import fitz
            doc = fitz.open()
            page = doc.new_page(width=612, height=792)
            # draw a little rect
            page.draw_rect(fitz.Rect(72, 72, 172, 92), color=(0, 0, 0), fill=(0, 0, 0))
            doc.save(str(pdf_path))
            doc.close()

            # Execute closed loop
            final_layout = agent.optimize_template_closed_loop(
                initial_layout,
                pdf_path,
                max_iterations=4,
                target_threshold=0.5,
            )

            # Assertions
            expect("ran 2 iterations of regression", len(regression_calls) == 2, f"got {len(regression_calls)}")
            expect("ran 1 iteration of agent refinement", len(refine_calls) == 1, f"got {len(refine_calls)}")
            
            # Check final style properties
            block = final_layout.blocks[0]
            expect("font size increased by +2pt (12 -> 14)", block.style.font_size_pt == 14.0, f"got {block.style.font_size_pt}")
            expect("alignment updated to center", block.style.align == "center", f"got {block.style.align}")
            expect("font weight updated to bold", block.style.font_weight == "bold", f"got {block.style.font_weight}")
            expect("margin top shifted by +10px (72 -> 82)", block.bbox.y_px == 82.0, f"got {block.bbox.y_px}")

    finally:
        docparser.render_layout_screenshot = saved_render
        docparser.calculate_visual_regression = saved_regression
        agent.agent_refine_layout_schema = saved_refine


if __name__ == "__main__":
    case_visual_regression_identical()
    case_visual_regression_differences()
    case_closed_loop_optimization()
    print()
    if _failures:
        print(f"FAIL: {len(_failures)} failures")
        for f in _failures:
            print(f"  - {f}")
        sys.exit(1)
    print("PASS: all closed-loop checks green")
