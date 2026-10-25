"""Discrete orientation operations. Never applied silently.

Orientation remaps original pixel coordinates of one image. The original
raster is never overwritten; the mapping is a project operation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from models.enums import OrientationName

# All candidates shown by TEST ORIENTATIONS.
ORIENTATION_CANDIDATES: tuple[OrientationName, ...] = (
    OrientationName.NORMAL,
    OrientationName.MIRROR_X,
    OrientationName.MIRROR_Y,
    OrientationName.ROTATE_90,
    OrientationName.ROTATE_180,
    OrientationName.ROTATE_270,
    OrientationName.MIRROR_X_ROTATE_90,
    OrientationName.MIRROR_X_ROTATE_180,
    OrientationName.MIRROR_X_ROTATE_270,
    OrientationName.MIRROR_Y_ROTATE_90,
)


@dataclass(frozen=True)
class Orientation:
    name: OrientationName = OrientationName.NORMAL
    mirror_x: bool = False
    mirror_y: bool = False
    rotate_quarter: int = 0  # 0,1,2,3 clockwise quarters after mirrors

    @classmethod
    def from_name(cls, name: OrientationName | str) -> "Orientation":
        name = OrientationName(name)
        table = {
            OrientationName.NORMAL: (False, False, 0),
            OrientationName.MIRROR_X: (True, False, 0),
            OrientationName.MIRROR_Y: (False, True, 0),
            OrientationName.ROTATE_90: (False, False, 1),
            OrientationName.ROTATE_180: (False, False, 2),
            OrientationName.ROTATE_270: (False, False, 3),
            OrientationName.MIRROR_X_ROTATE_90: (True, False, 1),
            OrientationName.MIRROR_X_ROTATE_180: (True, False, 2),
            OrientationName.MIRROR_X_ROTATE_270: (True, False, 3),
            OrientationName.MIRROR_Y_ROTATE_90: (False, True, 1),
            OrientationName.MIRROR_XY: (True, True, 0),
        }
        mx, my, rq = table.get(name, (False, False, 0))
        return cls(name=name, mirror_x=mx, mirror_y=my, rotate_quarter=rq)

    @classmethod
    def from_flags(cls, *, mirror_x: bool = False, mirror_y: bool = False, rotate_quarter: int = 0) -> "Orientation":
        rq = int(rotate_quarter) % 4
        mx, my = bool(mirror_x), bool(mirror_y)
        name = _name_from_flags(mx, my, rq)
        return cls(name=name, mirror_x=mx, mirror_y=my, rotate_quarter=rq)

    @classmethod
    def from_dict(cls, data: dict | None) -> "Orientation":
        if not data:
            return cls()
        if "mirror_x" in data or "mirror_y" in data or "rotate_quarter" in data:
            return cls.from_flags(
                mirror_x=bool(data.get("mirror_x", False)),
                mirror_y=bool(data.get("mirror_y", False)),
                rotate_quarter=int(data.get("rotate_quarter", 0)),
            )
        return cls.from_name(data.get("name", OrientationName.NORMAL))

    def toggled(self, *, flip_x: bool = False, flip_y: bool = False) -> "Orientation":
        return Orientation.from_flags(
            mirror_x=(not self.mirror_x) if flip_x else self.mirror_x,
            mirror_y=(not self.mirror_y) if flip_y else self.mirror_y,
            rotate_quarter=self.rotate_quarter,
        )

    def status_label(self) -> str:
        bits = []
        if self.mirror_x:
            bits.append("MIRROR X: ON")
        if self.mirror_y:
            bits.append("MIRROR Y: ON")
        if self.rotate_quarter:
            bits.append(f"ROTATE {self.rotate_quarter * 90}°")
        return "  ".join(bits) if bits else "NORMAL"

    def output_size(self, width: int, height: int) -> tuple[int, int]:
        if self.rotate_quarter % 2 == 1:
            return height, width
        return width, height

    def map_point(self, x: float, y: float, width: int, height: int) -> tuple[float, float]:
        """Original pixel → oriented pixel."""
        w, h = float(width), float(height)
        xx, yy = float(x), float(y)
        if self.mirror_x:
            xx = (w - 1.0) - xx
        if self.mirror_y:
            yy = (h - 1.0) - yy
        for _ in range(self.rotate_quarter % 4):
            # clockwise 90: (x,y) -> (h-1-y, x) after current size (w,h)
            xx, yy = (h - 1.0) - yy, xx
            w, h = h, w
        return xx, yy

    def unmap_point(self, x: float, y: float, width: int, height: int) -> tuple[float, float]:
        """Oriented pixel → original pixel. `width/height` are ORIGINAL size."""
        # Invert by applying inverse operations in reverse order.
        out_w, out_h = self.output_size(width, height)
        xx, yy = float(x), float(y)
        w, h = float(out_w), float(out_h)
        for _ in range(self.rotate_quarter % 4):
            # inverse of clockwise 90 is counter-clockwise 90
            # CCW: (x,y) -> (y, w-1-x) with current (w,h)
            xx, yy = yy, (w - 1.0) - xx
            w, h = h, w
        if self.mirror_y:
            yy = (h - 1.0) - yy
        if self.mirror_x:
            xx = (w - 1.0) - xx
        return xx, yy

    def map_points(self, points: np.ndarray, width: int, height: int) -> np.ndarray:
        pts = np.atleast_2d(np.asarray(points, dtype=np.float64))
        out = np.empty_like(pts)
        for i, (x, y) in enumerate(pts):
            out[i] = self.map_point(x, y, width, height)
        return out

    def as_dict(self) -> dict:
        return {
            "name": self.name.value,
            "mirror_x": self.mirror_x,
            "mirror_y": self.mirror_y,
            "rotate_quarter": self.rotate_quarter,
        }


def rotate_image_array(image: np.ndarray, orientation: Orientation) -> np.ndarray:
    """Return an oriented *copy* for display. Original must stay untouched."""
    if not orientation.mirror_x and not orientation.mirror_y and not orientation.rotate_quarter:
        return image
    out = image
    if orientation.mirror_x:
        out = np.fliplr(out)
    if orientation.mirror_y:
        out = np.flipud(out)
    if orientation.rotate_quarter:
        # np.rot90 is counter-clockwise; we defined clockwise quarters
        out = np.rot90(out, k=-orientation.rotate_quarter)
    return np.ascontiguousarray(out)


def reflection_required_for_opposite_sides(side_a: str, side_b: str) -> bool:
    return {side_a, side_b} == {"FRONT", "BACK"}


def _name_from_flags(mx: bool, my: bool, rq: int) -> OrientationName:
    table = {
        (False, False, 0): OrientationName.NORMAL,
        (True, False, 0): OrientationName.MIRROR_X,
        (False, True, 0): OrientationName.MIRROR_Y,
        (True, True, 0): OrientationName.MIRROR_XY,
        (False, False, 1): OrientationName.ROTATE_90,
        (False, False, 2): OrientationName.ROTATE_180,
        (False, False, 3): OrientationName.ROTATE_270,
        (True, False, 1): OrientationName.MIRROR_X_ROTATE_90,
        (True, False, 2): OrientationName.MIRROR_X_ROTATE_180,
        (True, False, 3): OrientationName.MIRROR_X_ROTATE_270,
        (False, True, 1): OrientationName.MIRROR_Y_ROTATE_90,
    }
    return table.get((mx, my, rq), OrientationName.NORMAL)


def orientation_label(name: OrientationName) -> str:
    labels = {
        OrientationName.NORMAL: "Normal",
        OrientationName.MIRROR_X: "Mirror X",
        OrientationName.MIRROR_Y: "Mirror Y",
        OrientationName.ROTATE_90: "Rotate 90°",
        OrientationName.ROTATE_180: "180°",
        OrientationName.ROTATE_270: "Rotate 270°",
        OrientationName.MIRROR_X_ROTATE_90: "Mirror X + 90°",
        OrientationName.MIRROR_X_ROTATE_180: "Mirror X + 180°",
        OrientationName.MIRROR_X_ROTATE_270: "Mirror X + 270°",
        OrientationName.MIRROR_Y_ROTATE_90: "Mirror Y + 90°",
        OrientationName.MIRROR_XY: "Mirror X+Y",
    }
    return labels.get(name, name.value)
