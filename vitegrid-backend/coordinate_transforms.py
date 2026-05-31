"""
Coordinate Projection Transforms

Bidirectional mapping between three coordinate spaces:
1. PDF space: Absolute unrotated page pixels (0,0 = bottom-left, Y increases upward)
2. VLM space: Normalized integer [0-1000]² representing virtual canvas (0,0 = top-left)
3. CSS space: Physical pixels in web viewport (0,0 = top-left)

References: High-Fidelity Document Reconstruction specification
"""

from typing import Tuple
import math


def pdf_to_vlm_coords(
    x_pdf: float, y_pdf: float, page_width_pt: float, page_height_pt: float
) -> Tuple[int, int]:
    """Convert PDF page coordinates to VLM normalized space [0-1000]²."""
    y_flipped = page_height_pt - y_pdf
    x_norm = (x_pdf / page_width_pt) * 1000
    y_norm = (y_flipped / page_height_pt) * 1000
    x_int = round(max(0, min(1000, x_norm)))
    y_int = round(max(0, min(1000, y_norm)))
    return x_int, y_int


def vlm_to_css_coords(
    x_norm: int, y_norm: int, viewport_width_px: float, viewport_height_px: float
) -> Tuple[float, float]:
    """Convert VLM normalized coordinates to CSS pixel space."""
    x_css = (x_norm / 1000.0) * viewport_width_px
    y_css = (y_norm / 1000.0) * viewport_height_px
    return x_css, y_css


def css_to_vlm_coords(
    x_css: float, y_css: float, viewport_width_px: float, viewport_height_px: float
) -> Tuple[int, int]:
    """Convert CSS pixel coordinates to VLM normalized space."""
    x_norm = (x_css / viewport_width_px) * 1000
    y_norm = (y_css / viewport_height_px) * 1000
    x_int = round(max(0, min(1000, x_norm)))
    y_int = round(max(0, min(1000, y_norm)))
    return x_int, y_int


def ioa_score(
    bbox1: Tuple[float, float, float, float], bbox2: Tuple[float, float, float, float]
) -> float:
    """Compute Intersection-over-Area (IoA) metric for two bounding boxes."""
    x0_1, y0_1, x1_1, y1_1 = bbox1
    x0_2, y0_2, x1_2, y1_2 = bbox2
    inter_x0 = max(x0_1, x0_2)
    inter_y0 = max(y0_1, y0_2)
    inter_x1 = min(x1_1, x1_2)
    inter_y1 = min(y1_1, y1_2)
    if inter_x1 <= inter_x0 or inter_y1 <= inter_y0:
        return 0.0
    inter_area = (inter_x1 - inter_x0) * (inter_y1 - inter_y0)
    bbox2_area = (x1_2 - x0_2) * (y1_2 - y0_2)
    if bbox2_area == 0:
        return 0.0
    return inter_area / bbox2_area
