"""Registration from corresponding points.

Measurement points NEVER enter these functions.
The caller is responsible for passing only enabled reference correspondences.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from core.residuals import PointResidual, residual_summary
from core.statistics import StatSummary
from core.transforms import AffineTransform, SimilarityTransform
from models.enums import TransformMode


class RegistrationError(ValueError):
    """User-facing registration failure."""


@dataclass
class RegistrationResult:
    transform: SimilarityTransform
    mode: TransformMode
    residuals: list[PointResidual] = field(default_factory=list)
    summary: StatSummary = field(default_factory=StatSummary)
    affine: AffineTransform | None = None
    notes: list[str] = field(default_factory=list)
    source_count: int = 0

    def as_dict(self) -> dict:
        return {
            "transform": self.transform.as_dict(),
            "mode": self.mode.value,
            "residuals": [r.as_dict() for r in self.residuals],
            "summary": self.summary.as_dict(),
            "affine": None if self.affine is None else self.affine.as_dict(),
            "notes": list(self.notes),
            "source_count": self.source_count,
        }


def _check(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    src = np.asarray(src, dtype=np.float64).reshape(-1, 2)
    dst = np.asarray(dst, dtype=np.float64).reshape(-1, 2)
    if src.shape != dst.shape:
        raise RegistrationError("Reference correspondences on A and B must have the same count.")
    if len(src) == 0:
        raise RegistrationError("At least 1 reference correspondence is required.")
    return src, dst


def translation_only(src: np.ndarray, dst: np.ndarray) -> SimilarityTransform:
    src, dst = _check(src, dst)
    delta = dst - src
    t = delta.mean(axis=0)
    return SimilarityTransform(
        scale=1.0, rotation_deg=0.0, tx=float(t[0]), ty=float(t[1]), mode=TransformMode.TRANSLATION
    )


def two_point_similarity(src: np.ndarray, dst: np.ndarray) -> SimilarityTransform:
    src, dst = _check(src, dst)
    if len(src) < 2:
        raise RegistrationError("At least 2 reference correspondences are required.")
    a0, a1 = src[0], src[1]
    b0, b1 = dst[0], dst[1]
    va = a1 - a0
    vb = b1 - b0
    la = float(np.linalg.norm(va))
    lb = float(np.linalg.norm(vb))
    if la < 1e-12:
        raise RegistrationError("Reference points P0 and P1 on A are coincident.")
    if lb < 1e-12:
        raise RegistrationError("Reference points P0 and P1 on B are coincident.")
    scale = lb / la
    ang_a = math.atan2(va[1], va[0])
    ang_b = math.atan2(vb[1], vb[0])
    rot = math.degrees(ang_b - ang_a)
    th = math.radians(rot)
    c, s = math.cos(th), math.sin(th)
    r = scale * np.array([[c, -s], [s, c]])
    t = b0 - r @ a0
    return SimilarityTransform(
        scale=scale, rotation_deg=rot, tx=float(t[0]), ty=float(t[1]), mode=TransformMode.SIMILARITY
    )


def umeyama(src: np.ndarray, dst: np.ndarray, *, with_scale: bool = True) -> SimilarityTransform:
    """Least-squares similarity / rigid (Umeyama 1991)."""
    src, dst = _check(src, dst)
    n = len(src)
    mu_s = src.mean(axis=0)
    mu_d = dst.mean(axis=0)
    src_c = src - mu_s
    dst_c = dst - mu_d
    cov = (dst_c.T @ src_c) / n
    u, svals, vt = np.linalg.svd(cov)
    r = u @ vt
    if np.linalg.det(r) < 0:
        vt = vt.copy()
        vt[-1, :] *= -1
        r = u @ vt
        svals = svals.copy()
        svals[-1] *= -1
    if with_scale:
        var_s = float((src_c**2).sum() / n)
        if var_s < 1e-18:
            raise RegistrationError("Reference points on A are coincident; scale is undefined.")
        scale = float(svals.sum() / var_s)
    else:
        scale = 1.0
    t = mu_d - scale * (r @ mu_s)
    rotation = math.degrees(math.atan2(r[1, 0], r[0, 0]))
    mode = TransformMode.SIMILARITY if with_scale else TransformMode.RIGID
    return SimilarityTransform(scale=scale, rotation_deg=rotation, tx=float(t[0]), ty=float(t[1]), mode=mode)


def affine_diagnostic(src: np.ndarray, dst: np.ndarray) -> AffineTransform:
    """DIAGNOSTIC ONLY — not physical proof."""
    src, dst = _check(src, dst)
    if len(src) < 3:
        raise RegistrationError("Affine diagnostic requires at least 3 reference correspondences.")
    # Solve [x y 1] @ [[a,c],[b,d],[tx,ty]] = [x', y']
    a_mat = np.hstack([src, np.ones((len(src), 1))])
    try:
        params, *_ = np.linalg.lstsq(a_mat, dst, rcond=None)
    except np.linalg.LinAlgError as exc:
        raise RegistrationError("Affine diagnostic failed to solve.") from exc
    m = np.eye(3, dtype=np.float64)
    m[0, 0] = params[0, 0]
    m[1, 0] = params[0, 1]
    m[0, 1] = params[1, 0]
    m[1, 1] = params[1, 1]
    m[0, 2] = params[2, 0]
    m[1, 2] = params[2, 1]
    return AffineTransform(matrix=m, diagnostic_only=True)


def fit(
    src: np.ndarray,
    dst: np.ndarray,
    mode: TransformMode,
    *,
    point_ids: list[str] | None = None,
) -> RegistrationResult:
    src, dst = _check(src, dst)
    n = len(src)
    notes: list[str] = []
    affine = None

    if mode == TransformMode.TRANSLATION:
        if n < 1:
            raise RegistrationError("At least 1 reference correspondence is required.")
        xf = translation_only(src, dst)
        if n >= 2:
            notes.append("Translation-only fit ignores rotation and scale implied by extra points.")
    elif mode == TransformMode.RIGID:
        if n < 2:
            raise RegistrationError("Rigid registration requires at least 2 reference correspondences.")
        xf = umeyama(src, dst, with_scale=False)
    elif mode == TransformMode.SIMILARITY:
        if n == 1:
            xf = translation_only(src, dst)
            notes.append("One correspondence: translation only. P0 becomes the displayed origin.")
        elif n == 2:
            xf = two_point_similarity(src, dst)
        else:
            xf = umeyama(src, dst, with_scale=True)
    elif mode == TransformMode.AFFINE:
        if n < 3:
            raise RegistrationError("Affine diagnostic requires at least 3 reference correspondences.")
        affine = affine_diagnostic(src, dst)
        # Also report a similarity for comparison — affine is diagnostic only.
        xf = umeyama(src, dst, with_scale=True)
        notes.append("AFFINE is DIAGNOSTIC ONLY and is not proof of physical compatibility.")
    else:
        raise RegistrationError(f"Unknown transform mode: {mode}")

    predicted = xf.apply(src)
    residuals = residual_summary(dst, predicted, point_ids)
    errors = [r.distance for r in residuals]
    from core.statistics import summarize

    return RegistrationResult(
        transform=xf,
        mode=mode,
        residuals=residuals,
        summary=summarize(errors),
        affine=affine,
        notes=notes,
        source_count=n,
    )


def choose_mode_for_count(n: int, requested: TransformMode | None = None) -> TransformMode:
    if requested is not None:
        return requested
    if n <= 1:
        return TransformMode.TRANSLATION
    if n == 2:
        return TransformMode.SIMILARITY
    return TransformMode.SIMILARITY
