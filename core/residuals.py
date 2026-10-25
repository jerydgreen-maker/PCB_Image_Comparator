"""Residual vectors and summaries. Individual errors are always retained."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from core.statistics import StatSummary, summarize


@dataclass
class PointResidual:
    point_id: str
    predicted_x: float
    predicted_y: float
    actual_x: float
    actual_y: float
    dx: float
    dy: float
    distance: float

    def as_dict(self) -> dict:
        return {
            "point_id": self.point_id,
            "predicted_x": self.predicted_x,
            "predicted_y": self.predicted_y,
            "actual_x": self.actual_x,
            "actual_y": self.actual_y,
            "dx": self.dx,
            "dy": self.dy,
            "distance": self.distance,
        }


def residual_summary(
    actual: np.ndarray, predicted: np.ndarray, point_ids: list[str] | None = None
) -> list[PointResidual]:
    act = np.asarray(actual, dtype=np.float64).reshape(-1, 2)
    pred = np.asarray(predicted, dtype=np.float64).reshape(-1, 2)
    if act.shape != pred.shape:
        raise ValueError("Actual and predicted arrays must match.")
    out: list[PointResidual] = []
    for i in range(len(act)):
        dx = float(act[i, 0] - pred[i, 0])
        dy = float(act[i, 1] - pred[i, 1])
        pid = point_ids[i] if point_ids and i < len(point_ids) else f"P{i:03d}"
        out.append(
            PointResidual(
                point_id=pid,
                predicted_x=float(pred[i, 0]),
                predicted_y=float(pred[i, 1]),
                actual_x=float(act[i, 0]),
                actual_y=float(act[i, 1]),
                dx=dx,
                dy=dy,
                distance=float(np.hypot(dx, dy)),
            )
        )
    return out


def residual_stats(residuals: list[PointResidual]) -> StatSummary:
    return summarize([r.distance for r in residuals])


def percent_le_tolerance(residuals: list[PointResidual], tolerance: float) -> float:
    if not residuals:
        return float("nan")
    hits = sum(1 for r in residuals if r.distance <= tolerance)
    return 100.0 * hits / len(residuals)
