"""CAD dimension geometry. Measured endpoints never move when the annotation is offset."""

from __future__ import annotations

import math

from models.dimension import Dimension, DimensionOrientation
from models.enums import Unit
from models.units import px_to_unit


def measured_delta(dim: Dimension) -> tuple[float, float]:
    dx = dim.p2x - dim.p1x
    dy = dim.p2y - dim.p1y
    if dim.orientation == DimensionOrientation.HORIZONTAL:
        return dx, 0.0
    if dim.orientation == DimensionOrientation.VERTICAL:
        return 0.0, dy
    return dx, dy


def measured_length_px(dim: Dimension) -> float:
    dx, dy = measured_delta(dim)
    return math.hypot(dx, dy)


def format_length(length_px: float, mm_per_px: float | None, unit: Unit, precision: int) -> str:
    prec = max(0, min(6, int(precision)))
    if unit == Unit.PX:
        return f"{length_px:.{prec}f} px"
    if mm_per_px is None or mm_per_px <= 0:
        return f"— {unit.value}"
    value = px_to_unit(length_px, mm_per_px, unit)
    if value is None:
        return f"— {unit.value}"
    return f"{value:.{prec}f} {unit.value}"


def annotation_line(dim: Dimension) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    """Return (q1, q2, text_anchor) for the dimension line, offset from P1–P2."""
    p1 = (dim.p1x, dim.p1y)
    p2 = (dim.p2x, dim.p2y)
    if dim.orientation == DimensionOrientation.HORIZONTAL:
        y = dim.offset_y if abs(dim.offset_y) > 1e-9 else (p1[1] + p2[1]) / 2.0
        q1 = (p1[0], y)
        q2 = (p2[0], y)
    elif dim.orientation == DimensionOrientation.VERTICAL:
        x = dim.offset_x if abs(dim.offset_x) > 1e-9 else (p1[0] + p2[0]) / 2.0
        q1 = (x, p1[1])
        q2 = (x, p2[1])
    else:
        dx = p2[0] - p1[0]
        dy = p2[1] - p1[1]
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length, dx / length
        ox, oy = dim.offset_x, dim.offset_y
        # Project the offset click onto the normal so the line stays parallel to P1–P2.
        dist = ox * nx + oy * ny
        q1 = (p1[0] + nx * dist, p1[1] + ny * dist)
        q2 = (p2[0] + nx * dist, p2[1] + ny * dist)
    mid = ((q1[0] + q2[0]) / 2.0 + dim.text_dx, (q1[1] + q2[1]) / 2.0 + dim.text_dy)
    return q1, q2, mid


def set_offset_from_click(dim: Dimension, x: float, y: float) -> None:
    """Third click / annotation move. Endpoints stay fixed."""
    if dim.orientation == DimensionOrientation.HORIZONTAL:
        dim.offset_x = 0.0
        dim.offset_y = y
    elif dim.orientation == DimensionOrientation.VERTICAL:
        dim.offset_x = x
        dim.offset_y = 0.0
    else:
        mid_x = (dim.p1x + dim.p2x) / 2.0
        mid_y = (dim.p1y + dim.p2y) / 2.0
        dim.offset_x = x - mid_x
        dim.offset_y = y - mid_y


def compare_lengths(
    length_a_px: float,
    length_b_px: float,
    mm_per_px_a: float | None,
    mm_per_px_b: float | None,
    unit: Unit,
    tolerance_value: float,
    tolerance_unit: Unit,
) -> dict:
    def to_display(px: float, scale: float | None) -> float:
        if unit == Unit.PX or scale is None or scale <= 0:
            return px
        out = px_to_unit(px, scale, unit)
        return px if out is None else out

    a = to_display(length_a_px, mm_per_px_a)
    b = to_display(length_b_px, mm_per_px_b)
    delta = b - a
    pct = (100.0 * delta / a) if a else None
    # Tolerance in the same display unit
    if tolerance_unit == unit:
        tol = tolerance_value
    elif tolerance_unit == Unit.PX and unit != Unit.PX and mm_per_px_b:
        conv = px_to_unit(tolerance_value, mm_per_px_b, unit)
        tol = conv if conv is not None else tolerance_value
    elif tolerance_unit != Unit.PX and unit == Unit.PX and mm_per_px_b:
        from models.units import mm_to_px
        from models.units import px_to_mm

        mm = tolerance_value if tolerance_unit == Unit.MM else None
        if mm is None:
            tol = tolerance_value
        else:
            px = mm_to_px(mm, mm_per_px_b)
            tol = px if px is not None else tolerance_value
    else:
        tol = tolerance_value
    status = "MATCH" if abs(delta) <= tol else "MISMATCH"
    return {
        "A_LENGTH": a,
        "B_LENGTH": b,
        "DELTA": delta,
        "PERCENT_DELTA": pct,
        "TOLERANCE": tol,
        "STATUS": status,
        "UNIT": unit.value,
    }
