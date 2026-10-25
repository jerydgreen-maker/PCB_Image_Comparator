"""Unit conversion. Pixels and physical units are never mixed silently."""

from __future__ import annotations

from models.enums import Unit

# Conversion of a physical unit into millimetres.
TO_MM = {
    Unit.MM: 1.0,
    Unit.CM: 10.0,
    Unit.INCH: 25.4,
    Unit.MIL: 0.0254,
}

FROM_MM = {unit: 1.0 / factor for unit, factor in TO_MM.items()}


def physical_to_mm(value: float, unit: Unit) -> float:
    if unit == Unit.PX:
        raise ValueError("Pixels are not a physical unit. Calibrate the image first.")
    return float(value) * TO_MM[unit]


def mm_to_physical(value_mm: float, unit: Unit) -> float:
    if unit == Unit.PX:
        raise ValueError("Pixels are not a physical unit.")
    return float(value_mm) * FROM_MM[unit]


def convert_physical(value: float, src: Unit, dst: Unit) -> float:
    if src == dst:
        return float(value)
    return mm_to_physical(physical_to_mm(value, src), dst)


def px_to_mm(px: float, mm_per_px: float | None) -> float | None:
    if mm_per_px is None:
        return None
    return float(px) * float(mm_per_px)


def mm_to_px(mm: float, mm_per_px: float | None) -> float | None:
    if mm_per_px is None or mm_per_px == 0:
        return None
    return float(mm) / float(mm_per_px)


def px_to_unit(px: float, mm_per_px: float | None, unit: Unit) -> float | None:
    if unit == Unit.PX:
        return float(px)
    mm = px_to_mm(px, mm_per_px)
    if mm is None:
        return None
    return mm_to_physical(mm, unit)


def unit_label(unit: Unit) -> str:
    return unit.value


def format_value(value: float | None, precision: int = 4, unit: Unit | None = None) -> str:
    if value is None:
        if unit is not None and unit != Unit.PX:
            return f"— {unit.value}"
        return "—"
    text = f"{value:.{precision}f}"
    if unit is not None:
        text += f" {unit.value}"
    return text


def format_physical_or_pending(value: float | None, unit: Unit, *, calibrated: bool, precision: int = 2) -> str:
    """User-facing physical value. Uncalibrated mm/mil/inch shows '— unit', never silent px."""
    if unit == Unit.PX:
        if value is None:
            return "—"
        return f"{value:.{precision}f} px"
    if not calibrated or value is None:
        return f"— {unit.value}"
    return f"{value:.{precision}f} {unit.value}"
