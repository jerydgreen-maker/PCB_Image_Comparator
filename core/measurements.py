"""Measurement-point residuals and group internal geometry.

Measurement points NEVER participate in registration.
Internal geometry is independent of absolute board position.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from core.geometry import (
    centroid,
    nearest_neighbor_distances,
    pairwise_distances,
    principal_orientation_deg,
    span_xy,
)
from core.residuals import PointResidual, residual_summary
from core.statistics import StatSummary, summarize
from core.transforms import SimilarityTransform
from models.enums import DiagnosticFlag, ResultState


@dataclass
class GroupGeometry:
    name: str
    count: int
    pairwise: list[float]
    nearest: list[float]
    span_x: float
    span_y: float
    centroid_xy: tuple[float, float]
    orientation_deg: float
    mean_pitch: float | None
    median_pitch: float | None
    row_spacing: float | None
    column_spacing: float | None
    horizontal_pitches: list[float] = field(default_factory=list)
    vertical_pitches: list[float] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "count": self.count,
            "span_x": self.span_x,
            "span_y": self.span_y,
            "centroid": self.centroid_xy,
            "orientation_deg": self.orientation_deg,
            "mean_pitch": self.mean_pitch,
            "median_pitch": self.median_pitch,
            "row_spacing": self.row_spacing,
            "column_spacing": self.column_spacing,
            "nearest": self.nearest,
            "pairwise_count": len(self.pairwise),
        }


@dataclass
class GroupComparison:
    name: str
    geometry_a: GroupGeometry | None
    geometry_b: GroupGeometry | None
    internal_state: ResultState
    board_dx: float | None
    board_dy: float | None
    board_distance: float | None
    pitch_delta: float | None
    flags: list[DiagnosticFlag] = field(default_factory=list)
    notes: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "internal_state": self.internal_state.value,
            "board_dx": self.board_dx,
            "board_dy": self.board_dy,
            "board_distance": self.board_distance,
            "pitch_delta": self.pitch_delta,
            "flags": [f.value for f in self.flags],
            "geometry_a": None if self.geometry_a is None else self.geometry_a.as_dict(),
            "geometry_b": None if self.geometry_b is None else self.geometry_b.as_dict(),
            "notes": self.notes,
        }


def predict_b(transform: SimilarityTransform, ax: float, ay: float) -> tuple[float, float]:
    return transform.apply_one(ax, ay)


def measurement_residual(
    transform: SimilarityTransform,
    ax: float,
    ay: float,
    bx: float,
    by: float,
    point_id: str,
) -> PointResidual:
    px, py = transform.apply_one(ax, ay)
    return residual_summary(
        np.array([[bx, by]], dtype=np.float64),
        np.array([[px, py]], dtype=np.float64),
        [point_id],
    )[0]


def _cluster_1d(values: np.ndarray, tol_ratio: float = 0.25) -> list[float]:
    if values.size == 0:
        return []
    order = np.sort(values)
    if order.size == 1:
        return [float(order[0])]
    gaps = np.diff(order)
    typical = float(np.median(gaps[gaps > 0])) if np.any(gaps > 0) else 0.0
    tol = typical * tol_ratio if typical > 0 else 1.0
    groups = [[float(order[0])]]
    for v in order[1:]:
        if abs(v - groups[-1][-1]) <= max(tol, 1e-6):
            groups[-1].append(float(v))
        else:
            groups.append([float(v)])
    return [float(np.mean(g)) for g in groups]


def analyze_group_geometry(name: str, points: np.ndarray) -> GroupGeometry:
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    n = len(pts)
    if n == 0:
        return GroupGeometry(
            name=name,
            count=0,
            pairwise=[],
            nearest=[],
            span_x=0.0,
            span_y=0.0,
            centroid_xy=(0.0, 0.0),
            orientation_deg=0.0,
            mean_pitch=None,
            median_pitch=None,
            row_spacing=None,
            column_spacing=None,
        )
    pair = pairwise_distances(pts)
    iu = np.triu_indices(n, k=1)
    pairwise = [float(v) for v in pair[iu]]
    nearest = [float(v) for v in nearest_neighbor_distances(pts)] if n >= 2 else []
    sx, sy = span_xy(pts)
    c = centroid(pts)
    orient = principal_orientation_deg(pts) if n >= 2 else 0.0

    xs = _cluster_1d(pts[:, 0])
    ys = _cluster_1d(pts[:, 1])
    col_pitch = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
    row_pitch = [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]
    pitches = nearest if nearest else pairwise
    return GroupGeometry(
        name=name,
        count=n,
        pairwise=pairwise,
        nearest=nearest,
        span_x=sx,
        span_y=sy,
        centroid_xy=(float(c[0]), float(c[1])),
        orientation_deg=orient,
        mean_pitch=float(np.mean(pitches)) if pitches else None,
        median_pitch=float(np.median(pitches)) if pitches else None,
        row_spacing=float(np.mean(row_pitch)) if row_pitch else None,
        column_spacing=float(np.mean(col_pitch)) if col_pitch else None,
        horizontal_pitches=col_pitch,
        vertical_pitches=row_pitch,
    )


def compare_group(
    name: str,
    points_a: np.ndarray,
    points_b: np.ndarray | None,
    *,
    transform: SimilarityTransform | None = None,
    match_tol: float | None = None,
) -> GroupComparison:
    ga = analyze_group_geometry(name, points_a) if len(points_a) else None
    gb = analyze_group_geometry(name, points_b) if points_b is not None and len(points_b) else None
    flags: list[DiagnosticFlag] = []
    internal = ResultState.UNVERIFIED
    pitch_delta = None
    if ga and gb and ga.mean_pitch is not None and gb.mean_pitch is not None:
        pitch_delta = gb.mean_pitch - ga.mean_pitch
        if match_tol is not None:
            internal = ResultState.MATCH if abs(pitch_delta) <= match_tol else ResultState.MISMATCH
            if abs(ga.span_x - gb.span_x) > match_tol or abs(ga.span_y - gb.span_y) > match_tol:
                flags.append(DiagnosticFlag.SIZE_MISMATCH)
            if abs(pitch_delta) > match_tol:
                flags.append(DiagnosticFlag.PITCH_MISMATCH)
        else:
            internal = ResultState.UNKNOWN
        if abs(ga.orientation_deg - gb.orientation_deg) > 5.0:
            flags.append(DiagnosticFlag.ROTATION_MISMATCH)

    board_dx = board_dy = board_d = None
    if ga and gb and transform is not None:
        pred = transform.apply(np.array([ga.centroid_xy], dtype=np.float64))[0]
        board_dx = gb.centroid_xy[0] - float(pred[0])
        board_dy = gb.centroid_xy[1] - float(pred[1])
        board_d = float(np.hypot(board_dx, board_dy))
        if match_tol is not None and board_d > match_tol:
            flags.append(DiagnosticFlag.POSITION_MISMATCH)

    return GroupComparison(
        name=name,
        geometry_a=ga,
        geometry_b=gb,
        internal_state=internal,
        board_dx=board_dx,
        board_dy=board_dy,
        board_distance=board_d,
        pitch_delta=pitch_delta,
        flags=flags,
        notes="Internal geometry is independent of board / global position.",
    )


def pitch_series(points: np.ndarray) -> dict:
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    if len(pts) < 2:
        raise ValueError("Pitch tool requires at least 2 ordered points.")
    diffs = pts[1:] - pts[:-1]
    pitches = np.hypot(diffs[:, 0], diffs[:, 1])
    stats = summarize(pitches)
    span = float(np.hypot(*(pts[-1] - pts[0])))
    return {
        "count": int(len(pts)),
        "individual_pitch": [float(v) for v in pitches],
        "mean": stats.mean,
        "median": stats.median,
        "min": stats.minimum,
        "max": stats.maximum,
        "std": stats.std,
        "total_span": span,
    }


def fit_circle(points: np.ndarray) -> dict:
    """Algebraic circle fit (Kåsa). Requires 3+ edge points."""
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    if len(pts) < 3:
        raise ValueError("Circle fit requires at least 3 edge points.")
    x = pts[:, 0]
    y = pts[:, 1]
    a_mat = np.column_stack([2 * x, 2 * y, np.ones(len(pts))])
    b = x**2 + y**2
    sol, *_ = np.linalg.lstsq(a_mat, b, rcond=None)
    cx, cy, c = sol
    radius = math_hypot_safe(cx, cy, c)
    residuals = np.hypot(x - cx, y - cy) - radius
    return {
        "center": (float(cx), float(cy)),
        "radius": float(radius),
        "diameter": float(2 * radius),
        "fit_residual_rmse": float(np.sqrt(np.mean(residuals**2))),
        "residuals": [float(v) for v in residuals],
        "count": int(len(pts)),
    }


def math_hypot_safe(cx: float, cy: float, c: float) -> float:
    val = cx * cx + cy * cy + c
    return float(np.sqrt(max(val, 0.0)))
