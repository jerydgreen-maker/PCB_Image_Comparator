"""Diagnostic suspicions only. Never auto-correct the accepted transform."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from core.orientation import ORIENTATION_CANDIDATES, Orientation
from core.registration import fit
from core.residuals import PointResidual
from models.enums import DiagnosticFlag, TransformMode


@dataclass
class DiagnosticFinding:
    flag: DiagnosticFlag
    message: str
    evidence: dict = field(default_factory=dict)
    auto_fix_forbidden: bool = True

    def as_dict(self) -> dict:
        return {
            "flag": self.flag.value,
            "message": self.message,
            "evidence": self.evidence,
            "auto_fix_forbidden": True,
        }


def common_translation(group_residuals: dict[str, list[PointResidual]], *, agree_tol: float) -> DiagnosticFinding | None:
    """If several groups share a similar residual translation, flag registration error."""
    means = []
    for name, res in group_residuals.items():
        if not res:
            continue
        dx = float(np.mean([r.dx for r in res]))
        dy = float(np.mean([r.dy for r in res]))
        means.append((name, dx, dy))
    if len(means) < 2:
        return None
    arr = np.array([[dx, dy] for _, dx, dy in means], dtype=np.float64)
    spread = arr.std(axis=0)
    common = arr.mean(axis=0)
    common_norm = float(np.hypot(*common))
    if common_norm < agree_tol:
        return None
    if float(np.hypot(*spread)) > agree_tol and float(np.hypot(*spread)) > 0.5 * common_norm:
        return None
    return DiagnosticFinding(
        flag=DiagnosticFlag.POSSIBLE_GLOBAL_REGISTRATION_ERROR,
        message="POSSIBLE GLOBAL REGISTRATION ERROR — groups share a common translation. Diagnostic only; not subtracted.",
        evidence={
            "group_means": {n: {"dx": dx, "dy": dy} for n, dx, dy in means},
            "common_dx": float(common[0]),
            "common_dy": float(common[1]),
        },
    )


def scale_mismatch(residuals: list[PointResidual], origins: np.ndarray) -> DiagnosticFinding | None:
    """If residual magnitude grows with distance from origin, suspect scale."""
    if len(residuals) < 4:
        return None
    r = np.array([rec.distance for rec in residuals], dtype=np.float64)
    d = np.hypot(origins[:, 0], origins[:, 1])
    if float(d.std()) < 1e-9:
        return None
    corr = float(np.corrcoef(d, r)[0, 1])
    if not np.isfinite(corr) or corr < 0.55:
        return None
    return DiagnosticFinding(
        flag=DiagnosticFlag.POSSIBLE_SCALE_ERROR,
        message="POSSIBLE SCALE ERROR — residual magnitude increases with distance from origin. Not auto-corrected.",
        evidence={"correlation": corr},
    )


def rotation_mismatch(residuals: list[PointResidual], origins: np.ndarray) -> DiagnosticFinding | None:
    """Tangential residual vs radius suggests leftover rotation."""
    if len(residuals) < 4:
        return None
    tang = []
    rad = []
    for rec, o in zip(residuals, origins):
        radius = float(np.hypot(o[0], o[1]))
        if radius < 1e-6:
            continue
        ux, uy = o[0] / radius, o[1] / radius
        # tangential unit: (-uy, ux)
        tang.append(-uy * rec.dx + ux * rec.dy)
        rad.append(radius)
    if len(tang) < 4:
        return None
    tang_a = np.array(tang)
    rad_a = np.array(rad)
    # angle ≈ tangential / radius
    angles = tang_a / np.maximum(rad_a, 1e-9)
    if float(np.std(angles)) > 0.15:
        return None
    mean_ang = float(np.mean(angles))
    if abs(mean_ang) < 0.002:
        return None
    return DiagnosticFinding(
        flag=DiagnosticFlag.POSSIBLE_ROTATION_ERROR,
        message="POSSIBLE ROTATION ERROR — residual directions form a rotational pattern. Not auto-corrected.",
        evidence={"mean_angle_rad": mean_ang, "mean_angle_deg": float(np.degrees(mean_ang))},
    )


def mirror_improvement(
    src: np.ndarray,
    dst: np.ndarray,
    width_b: int,
    height_b: int,
    mode: TransformMode,
) -> DiagnosticFinding | None:
    """If a reflection strongly improves RMSE, flag possible orientation error."""
    if len(src) < 2:
        return None
    try:
        current = fit(src, dst, mode)
    except Exception:
        return None
    current_rmse = current.summary.rmse or float("inf")
    best_name = "NORMAL"
    best_rmse = current_rmse
    table = []
    for name in ORIENTATION_CANDIDATES:
        orient = Orientation.from_name(name)
        mapped = orient.map_points(dst, width_b, height_b)
        try:
            result = fit(src, mapped, mode)
        except Exception:
            continue
        rmse = result.summary.rmse if result.summary.rmse is not None else float("inf")
        table.append((name.value, rmse))
        if rmse < best_rmse:
            best_rmse = rmse
            best_name = name.value
    if best_name == "NORMAL" or not np.isfinite(best_rmse):
        return None
    if current_rmse <= 0 or best_rmse > 0.5 * current_rmse:
        return None
    return DiagnosticFinding(
        flag=DiagnosticFlag.POSSIBLE_MIRROR_ORIENTATION_ERROR,
        message="POSSIBLE MIRROR/ORIENTATION ERROR — a reflection/rotation candidate improves RMSE. Confirmation required.",
        evidence={
            "current_rmse": current_rmse,
            "best_orientation": best_name,
            "best_rmse": best_rmse,
            "candidates": table,
        },
    )


def evaluate_orientations(
    src: np.ndarray,
    dst: np.ndarray,
    width_b: int,
    height_b: int,
    mode: TransformMode,
    meas_src: np.ndarray | None = None,
    meas_dst: np.ndarray | None = None,
) -> list[dict]:
    rows = []
    for name in ORIENTATION_CANDIDATES:
        orient = Orientation.from_name(name)
        mapped = orient.map_points(dst, width_b, height_b)
        try:
            result = fit(src, mapped, mode)
            ref_rmse = result.summary.rmse
        except Exception as exc:
            rows.append(
                {
                    "orientation": name.value,
                    "reference_rmse": None,
                    "measurement_rmse": None,
                    "n_points": len(src),
                    "error": str(exc),
                }
            )
            continue
        meas_rmse = None
        if meas_src is not None and meas_dst is not None and len(meas_src) and len(meas_dst):
            mapped_m = orient.map_points(meas_dst, width_b, height_b)
            pred = result.transform.apply(meas_src)
            meas_rmse = float(np.sqrt(np.mean(np.sum((mapped_m - pred) ** 2, axis=1))))
        rows.append(
            {
                "orientation": name.value,
                "reference_rmse": ref_rmse,
                "measurement_rmse": meas_rmse,
                "n_points": len(src),
                "scale": result.transform.scale,
                "rotation_deg": result.transform.rotation_deg,
            }
        )
    finite = [r for r in rows if r.get("reference_rmse") is not None]
    if finite:
        best = min(finite, key=lambda r: r["reference_rmse"])
        for r in rows:
            r["best"] = r is best
    return rows
