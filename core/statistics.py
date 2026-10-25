"""Descriptive statistics. Never hide individual errors behind only an average."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class StatSummary:
    count: int = 0
    mean: float | None = None
    median: float | None = None
    std: float | None = None
    rmse: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    values: list[float] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "count": self.count,
            "mean": self.mean,
            "median": self.median,
            "std": self.std,
            "rmse": self.rmse,
            "min": self.minimum,
            "max": self.maximum,
        }


def summarize(values: list[float] | np.ndarray) -> StatSummary:
    arr = np.asarray(list(values), dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return StatSummary(count=0, values=[])
    return StatSummary(
        count=int(arr.size),
        mean=float(arr.mean()),
        median=float(np.median(arr)),
        std=float(arr.std(ddof=1)) if arr.size > 1 else 0.0,
        rmse=float(np.sqrt(np.mean(arr**2))),
        minimum=float(arr.min()),
        maximum=float(arr.max()),
        values=[float(v) for v in arr],
    )


def rmse(values: list[float] | np.ndarray) -> float:
    arr = np.asarray(values, dtype=np.float64)
    if arr.size == 0:
        return float("nan")
    return float(np.sqrt(np.mean(arr**2)))


def percent_within(values: list[float] | np.ndarray, tolerance: float) -> float:
    arr = np.asarray(values, dtype=np.float64)
    if arr.size == 0:
        return float("nan")
    return float(100.0 * np.mean(np.abs(arr) <= tolerance))


def modified_z_scores(values: list[float] | np.ndarray) -> np.ndarray:
    """Robust outlier scores. Highlight only — never auto-remove."""
    arr = np.asarray(values, dtype=np.float64)
    if arr.size == 0:
        return arr
    med = np.median(arr)
    mad = np.median(np.abs(arr - med))
    if mad < 1e-15:
        return np.zeros_like(arr)
    return 0.6745 * (arr - med) / mad
