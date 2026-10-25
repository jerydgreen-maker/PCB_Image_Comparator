"""Explicit 2D transforms. Nothing here mutates images.

A similarity maps A-space → B-space:

    p_b = s * R(theta) @ p_a + t

Affine is DIAGNOSTIC ONLY and is never treated as physical proof.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from models.enums import TransformMode


@dataclass
class SimilarityTransform:
    scale: float = 1.0
    rotation_deg: float = 0.0
    tx: float = 0.0
    ty: float = 0.0
    mode: TransformMode = TransformMode.SIMILARITY

    def rotation_rad(self) -> float:
        return math.radians(self.rotation_deg)

    def matrix(self) -> np.ndarray:
        th = self.rotation_rad()
        c, s = math.cos(th), math.sin(th)
        sc = self.scale
        return np.array(
            [
                [sc * c, -sc * s, self.tx],
                [sc * s, sc * c, self.ty],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        )

    def inverse(self) -> "SimilarityTransform":
        th = self.rotation_rad()
        c, s = math.cos(th), math.sin(th)
        sc = self.scale
        if abs(sc) < 1e-18:
            raise ValueError("Transform scale is zero; inverse is undefined.")
        # inverse of s*R is (1/s)*R^T
        r00, r01 = sc * c, -sc * s
        r10, r11 = sc * s, sc * c
        inv = np.array([[r00, r01], [r10, r11]], dtype=np.float64)
        inv = np.linalg.inv(inv)
        t = -inv @ np.array([self.tx, self.ty], dtype=np.float64)
        # Recover scale/rotation of inverse
        inv_scale = 1.0 / sc
        inv_rot = -self.rotation_deg
        return SimilarityTransform(
            scale=inv_scale, rotation_deg=inv_rot, tx=float(t[0]), ty=float(t[1]), mode=self.mode
        )

    def apply(self, points: np.ndarray) -> np.ndarray:
        pts = np.asarray(points, dtype=np.float64)
        single = pts.ndim == 1
        pts = np.atleast_2d(pts)
        m = self.matrix()
        hom = np.hstack([pts, np.ones((len(pts), 1))])
        out = (m @ hom.T).T[:, :2]
        return out[0] if single else out

    def apply_one(self, x: float, y: float) -> tuple[float, float]:
        p = self.apply(np.array([x, y], dtype=np.float64))
        return float(p[0]), float(p[1])

    def as_dict(self) -> dict:
        return {
            "scale": self.scale,
            "rotation_deg": self.rotation_deg,
            "tx": self.tx,
            "ty": self.ty,
            "mode": self.mode.value,
            "matrix": self.matrix().tolist(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SimilarityTransform":
        return cls(
            scale=float(data.get("scale", 1.0)),
            rotation_deg=float(data.get("rotation_deg", 0.0)),
            tx=float(data.get("tx", 0.0)),
            ty=float(data.get("ty", 0.0)),
            mode=TransformMode(data.get("mode", TransformMode.SIMILARITY.value)),
        )


@dataclass
class AffineTransform:
    """DIAGNOSTIC ONLY. Never use as proof of physical compatibility."""

    matrix: np.ndarray = field(
        default_factory=lambda: np.eye(3, dtype=np.float64)
    )
    diagnostic_only: bool = True

    def apply(self, points: np.ndarray) -> np.ndarray:
        pts = np.asarray(points, dtype=np.float64)
        single = pts.ndim == 1
        pts = np.atleast_2d(pts)
        hom = np.hstack([pts, np.ones((len(pts), 1))])
        out = (self.matrix @ hom.T).T[:, :2]
        return out[0] if single else out

    def decompose(self) -> dict:
        a, b, tx = self.matrix[0]
        c, d, ty = self.matrix[1]
        scale_x = math.hypot(a, c)
        scale_y = math.hypot(b, d)
        rotation = math.degrees(math.atan2(c, a)) if scale_x else 0.0
        # shear: angle between axes
        det = a * d - b * c
        shear = 0.0
        if scale_x > 1e-12 and scale_y > 1e-12:
            shear = (a * b + c * d) / (scale_x * scale_y)
        return {
            "scale_x": scale_x,
            "scale_y": scale_y,
            "shear": shear,
            "rotation_deg": rotation,
            "tx": float(tx),
            "ty": float(ty),
            "determinant": float(det),
            "diagnostic_only": True,
        }

    def as_dict(self) -> dict:
        d = {"matrix": self.matrix.tolist(), "diagnostic_only": True}
        d.update(self.decompose())
        return d


def identity() -> SimilarityTransform:
    return SimilarityTransform()


def translation(tx: float, ty: float) -> SimilarityTransform:
    return SimilarityTransform(scale=1.0, rotation_deg=0.0, tx=tx, ty=ty, mode=TransformMode.TRANSLATION)


def compose_similarity(second: SimilarityTransform, first: SimilarityTransform) -> SimilarityTransform:
    """Apply first, then second."""
    m = second.matrix() @ first.matrix()
    scale = math.hypot(m[0, 0], m[1, 0])
    rotation = math.degrees(math.atan2(m[1, 0], m[0, 0]))
    return SimilarityTransform(
        scale=scale, rotation_deg=rotation, tx=float(m[0, 2]), ty=float(m[1, 2]), mode=second.mode
    )
