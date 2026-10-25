"""Propagation of calibration and point-selection uncertainty.

Do not display more digits than the uncertainty supports.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from core.calibration import Calibration
from models.enums import Unit
from models.units import physical_to_mm


@dataclass
class UncertaintyBudget:
    scale_mm_per_px: float | None
    calibration_length_mm: float | None
    calibration_length_unc_mm: float | None
    pixel_distance: float | None
    point_selection_px: float
    distance_uncertainty_mm: float | None
    display_precision: int

    def as_dict(self) -> dict:
        return {
            "scale_mm_per_px": self.scale_mm_per_px,
            "calibration_length_mm": self.calibration_length_mm,
            "calibration_length_unc_mm": self.calibration_length_unc_mm,
            "pixel_distance": self.pixel_distance,
            "point_selection_px": self.point_selection_px,
            "distance_uncertainty_mm": self.distance_uncertainty_mm,
            "display_precision": self.display_precision,
        }


def sensible_precision(uncertainty: float | None, *, fallback: int = 4) -> int:
    if uncertainty is None or uncertainty <= 0 or not math.isfinite(uncertainty):
        return fallback
    # One significant figure of uncertainty → digits after decimal
    digits = -int(math.floor(math.log10(uncertainty)))
    return int(max(0, min(6, digits)))


def format_with_uncertainty(value: float, uncertainty: float | None, unit: str = "") -> str:
    prec = sensible_precision(uncertainty)
    if uncertainty is None:
        text = f"{value:.{prec}f}"
    else:
        text = f"{value:.{prec}f} ± {uncertainty:.{prec}f}"
    if unit:
        text += f" {unit}"
    return text


def calibration_uncertainty(cal: Calibration) -> UncertaintyBudget:
    if not cal.is_calibrated:
        return UncertaintyBudget(
            scale_mm_per_px=None,
            calibration_length_mm=None,
            calibration_length_unc_mm=None,
            pixel_distance=cal.pixel_distance,
            point_selection_px=cal.point_selection_px,
            distance_uncertainty_mm=None,
            display_precision=4,
        )
    known_mm = None
    unc_mm = None
    if cal.known_length is not None:
        known_mm = physical_to_mm(cal.known_length, cal.known_unit)
        if cal.length_uncertainty is not None:
            unc_mm = physical_to_mm(cal.length_uncertainty, cal.known_unit)

    # Distance between two independently picked points: sqrt(2)*pick_unc in px
    pick_px = cal.point_selection_px
    pixel_dist = cal.pixel_distance or 0.0
    scale = cal.mm_per_px or 0.0

    # Combined relative uncertainty of scale
    rel_terms = []
    if known_mm and unc_mm:
        rel_terms.append((unc_mm / known_mm) ** 2)
    if pixel_dist > 0:
        rel_terms.append((math.sqrt(2.0) * pick_px / pixel_dist) ** 2)
    rel = math.sqrt(sum(rel_terms)) if rel_terms else 0.0
    scale_unc = scale * rel

    # Uncertainty of a typical measured length of `pixel_dist` (the cal length)
    dist_unc_mm = None
    if known_mm is not None:
        # measured_mm = px * scale; unc from scale and two endpoints
        endpoint = math.sqrt(2.0) * pick_px * scale
        scale_part = pixel_dist * scale_unc
        dist_unc_mm = math.hypot(endpoint, scale_part)

    return UncertaintyBudget(
        scale_mm_per_px=scale,
        calibration_length_mm=known_mm,
        calibration_length_unc_mm=unc_mm,
        pixel_distance=cal.pixel_distance,
        point_selection_px=pick_px,
        distance_uncertainty_mm=dist_unc_mm,
        display_precision=sensible_precision(dist_unc_mm),
    )


def residual_uncertainty_mm(cal: Calibration, residual_px: float) -> tuple[float | None, float | None]:
    """Return (value_mm, uncertainty_mm) for a residual measured in pixels on this image."""
    if not cal.is_calibrated or cal.mm_per_px is None:
        return None, None
    value = residual_px * cal.mm_per_px
    budget = calibration_uncertainty(cal)
    # residual of two points (predicted vs actual) → two picks
    pick = math.sqrt(2.0) * cal.point_selection_px * cal.mm_per_px
    scale_part = residual_px * (budget.distance_uncertainty_mm or 0.0) / max(cal.pixel_distance or 1.0, 1e-9)
    # simpler: pick-dominated for small residuals
    unc = math.hypot(pick, abs(residual_px) * (cal.mm_per_px * 0.0 if budget.calibration_length_unc_mm is None else 0.0))
    if budget.calibration_length_mm and budget.calibration_length_unc_mm and cal.pixel_distance:
        rel = budget.calibration_length_unc_mm / budget.calibration_length_mm
        unc = math.hypot(pick, abs(value) * rel)
    return value, unc
