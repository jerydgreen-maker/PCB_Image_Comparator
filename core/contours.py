"""Contour comparison, residuals, signed deviation, and width cross-sections."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from core.geometry import (
    line_polyline_intersections,
    point_to_polyline_distance,
    polyline_normals,
    resample_polyline,
    as_points,
)
from core.registration import RegistrationResult, fit
from core.residuals import PointResidual
from core.statistics import StatSummary, percent_within, summarize
from core.transforms import SimilarityTransform
from models.enums import Confidence, CrossSectionAxis, TransformMode


DEFAULT_WIDTH_TOLERANCES_MM = (0.1, 0.25, 0.5, 1.0)


@dataclass
class ContourResidual:
    index: int
    ax: float
    ay: float
    bx: float
    by: float
    dx: float
    dy: float
    distance: float
    signed: float | None
    classification: str | None  # B_OUTSIDE_A / B_INSIDE_A
    segment_index: int
    confidence: str

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "ax": self.ax,
            "ay": self.ay,
            "bx": self.bx,
            "by": self.by,
            "dx": self.dx,
            "dy": self.dy,
            "distance": self.distance,
            "signed": self.signed,
            "classification": self.classification,
            "segment_index": self.segment_index,
            "confidence": self.confidence,
        }


@dataclass
class CrossSection:
    axis: CrossSectionAxis
    position: float
    a_left: float | None
    a_right: float | None
    a_width: float | None
    b_left: float | None
    b_right: float | None
    b_width: float | None
    delta_width: float | None

    def as_dict(self) -> dict:
        return {
            "axis": self.axis.value,
            "position": self.position,
            "a_left": self.a_left,
            "a_right": self.a_right,
            "a_width": self.a_width,
            "b_left": self.b_left,
            "b_right": self.b_right,
            "b_width": self.b_width,
            "delta_width": self.delta_width,
        }


def _allowed(conf: str, include: set[str]) -> bool:
    return conf in include


def contour_point_residuals(
    contour_a: np.ndarray,
    contour_b: np.ndarray,
    *,
    closed_a: bool = True,
    closed_b: bool = True,
    confidence_a: list[str] | None = None,
    include_confidence: set[str] | None = None,
    sample_count: int = 200,
) -> list[ContourResidual]:
    include = include_confidence or {Confidence.CONFIRMED.value, Confidence.CONFIRMED_VISIBLE.value}
    pts_a = as_points(contour_a)
    pts_b = as_points(contour_b)
    if len(pts_a) < 2 or len(pts_b) < 2:
        raise ValueError("Both contours need at least 2 points.")
    sampled = resample_polyline(pts_a, sample_count, closed=closed_a)
    normals = polyline_normals(pts_a, closed=closed_a)
    # Map sampled points to nearest original vertex for confidence/normal
    residuals: list[ContourResidual] = []
    for i, p in enumerate(sampled):
        # nearest original vertex for confidence
        dvert = np.hypot(*(pts_a - p).T)
        vi = int(np.argmin(dvert))
        conf = (
            confidence_a[min(vi, len(confidence_a) - 1)]
            if confidence_a
            else Confidence.CONFIRMED_VISIBLE.value
        )
        if not _allowed(conf, include):
            continue
        dist, closest, seg_i = point_to_polyline_distance(p, pts_b, closed=closed_b)
        delta = closest - p
        signed = None
        classification = None
        if closed_a:
            n = normals[min(vi, len(normals) - 1)]
            nlen = float(np.linalg.norm(n))
            if nlen > 0:
                signed = float(np.dot(delta, n / nlen))
                if signed > 0:
                    classification = "B_OUTSIDE_A"
                elif signed < 0:
                    classification = "B_INSIDE_A"
        residuals.append(
            ContourResidual(
                index=i,
                ax=float(p[0]),
                ay=float(p[1]),
                bx=float(closest[0]),
                by=float(closest[1]),
                dx=float(delta[0]),
                dy=float(delta[1]),
                distance=float(dist),
                signed=signed,
                classification=classification,
                segment_index=seg_i,
                confidence=conf,
            )
        )
    return residuals


def contour_residual_report(
    residuals: list[ContourResidual], tolerances: tuple[float, ...] = DEFAULT_WIDTH_TOLERANCES_MM
) -> dict:
    dists = [r.distance for r in residuals]
    signed = [r.signed for r in residuals if r.signed is not None]
    stats = summarize(dists)
    by_seg: dict[int, list[float]] = {}
    for r in residuals:
        by_seg.setdefault(r.segment_index, []).append(r.distance)
    return {
        "count": stats.count,
        "mean": stats.mean,
        "median": stats.median,
        "rmse": stats.rmse,
        "max": stats.maximum,
        "min": stats.minimum,
        "signed_mean": float(np.mean(signed)) if signed else None,
        "outside_count": sum(1 for r in residuals if r.classification == "B_OUTSIDE_A"),
        "inside_count": sum(1 for r in residuals if r.classification == "B_INSIDE_A"),
        "percent_le_tolerance": {str(t): percent_within(dists, t) for t in tolerances},
        "by_segment": {str(k): summarize(v).as_dict() for k, v in by_seg.items()},
    }


def register_contours(
    contour_a: np.ndarray,
    contour_b: np.ndarray,
    *,
    mode: TransformMode = TransformMode.SIMILARITY,
    closed: bool = True,
    sample_count: int = 80,
) -> RegistrationResult:
    if mode not in (TransformMode.RIGID, TransformMode.SIMILARITY):
        raise ValueError("Contour registration permits rigid or similarity only. Deformation is not allowed.")
    a = resample_polyline(as_points(contour_a), sample_count, closed=closed)
    b = resample_polyline(as_points(contour_b), sample_count, closed=closed)
    if closed and len(a) >= 3 and len(b) >= 3:
        # Try circular shifts of B to find the best correspondence start
        best = None
        best_rmse = float("inf")
        for shift in range(len(b)):
            shifted = np.roll(b, shift, axis=0)
            try:
                result = fit(a, shifted, mode)
            except Exception:
                continue
            rmse = result.summary.rmse if result.summary.rmse is not None else float("inf")
            if rmse < best_rmse:
                best_rmse = rmse
                best = result
        if best is None:
            raise ValueError("Contour registration failed.")
        return best
    return fit(a, b, mode)


def width_cross_section(
    contour_a: np.ndarray,
    contour_b: np.ndarray,
    position: float,
    axis: CrossSectionAxis,
    *,
    closed_a: bool = True,
    closed_b: bool = True,
) -> CrossSection:
    if axis == CrossSectionAxis.HORIZONTAL:
        origin = np.array([0.0, position], dtype=np.float64)
        direction = np.array([1.0, 0.0], dtype=np.float64)
    else:
        origin = np.array([position, 0.0], dtype=np.float64)
        direction = np.array([0.0, 1.0], dtype=np.float64)

    def extents(poly, closed):
        hits = line_polyline_intersections(origin, direction, poly, closed=closed)
        if len(hits) < 2:
            return None, None, None
        if axis == CrossSectionAxis.HORIZONTAL:
            xs = sorted(float(h[0]) for h in hits)
            return xs[0], xs[-1], xs[-1] - xs[0]
        ys = sorted(float(h[1]) for h in hits)
        return ys[0], ys[-1], ys[-1] - ys[0]

    a_lo, a_hi, a_w = extents(contour_a, closed_a)
    b_lo, b_hi, b_w = extents(contour_b, closed_b)
    delta = None if a_w is None or b_w is None else b_w - a_w
    if a_w is None:
        raise ValueError(f"No valid contour intersections at {'Y' if axis == CrossSectionAxis.HORIZONTAL else 'X'} = {position}.")
    return CrossSection(
        axis=axis,
        position=position,
        a_left=a_lo,
        a_right=a_hi,
        a_width=a_w,
        b_left=b_lo,
        b_right=b_hi,
        b_width=b_w,
        delta_width=delta,
    )


def automatic_cross_sections(
    contour_a: np.ndarray,
    contour_b: np.ndarray,
    *,
    axis: CrossSectionAxis = CrossSectionAxis.HORIZONTAL,
    step: float = 1.0,
    closed_a: bool = True,
    closed_b: bool = True,
) -> list[CrossSection]:
    pts = as_points(contour_a)
    if axis == CrossSectionAxis.HORIZONTAL:
        lo, hi = float(pts[:, 1].min()), float(pts[:, 1].max())
    else:
        lo, hi = float(pts[:, 0].min()), float(pts[:, 0].max())
    if step <= 0:
        raise ValueError("Cross-section step must be positive.")
    positions = np.arange(lo + step, hi, step)
    out: list[CrossSection] = []
    for pos in positions:
        try:
            out.append(
                width_cross_section(
                    contour_a, contour_b, float(pos), axis, closed_a=closed_a, closed_b=closed_b
                )
            )
        except ValueError:
            continue
    return out
