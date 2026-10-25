"""Optional snap helpers. Proposed positions must be confirmed — never silent."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from models.enums import SnapMode


@dataclass
class SnapProposal:
    mode: SnapMode
    x: float
    y: float
    score: float
    description: str

    def as_dict(self) -> dict:
        return {
            "mode": self.mode.value,
            "x": self.x,
            "y": self.y,
            "score": self.score,
            "description": self.description,
        }


def _gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    import cv2

    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def propose_snap(
    image: np.ndarray, x: float, y: float, mode: SnapMode, *, radius: int = 24
) -> SnapProposal | None:
    if mode == SnapMode.NONE or image is None:
        return None
    import cv2

    gray = _gray(image)
    h, w = gray.shape[:2]
    ix, iy = int(round(x)), int(round(y))
    if not (0 <= ix < w and 0 <= iy < h):
        return None
    x0, x1 = max(0, ix - radius), min(w, ix + radius + 1)
    y0, y1 = max(0, iy - radius), min(h, iy + radius + 1)
    roi = gray[y0:y1, x0:x1]
    if roi.size == 0:
        return None

    if mode == SnapMode.EDGE:
        edges = cv2.Canny(roi, 60, 180)
        ys, xs = np.where(edges > 0)
        if len(xs) == 0:
            return None
        d = (xs - (ix - x0)) ** 2 + (ys - (iy - y0)) ** 2
        k = int(np.argmin(d))
        return SnapProposal(
            SnapMode.EDGE,
            float(xs[k] + x0) + 0.5,
            float(ys[k] + y0) + 0.5,
            float(np.sqrt(d[k])),
            "Nearest Canny edge pixel (proposal — confirm required)",
        )

    if mode in (SnapMode.CIRCLE_CENTER, SnapMode.PAD_CENTER):
        min_r = 3
        max_r = max(min_r + 1, radius)
        circles = cv2.HoughCircles(
            roi,
            cv2.HOUGH_GRADIENT,
            dp=1.2,
            minDist=8,
            param1=120,
            param2=18,
            minRadius=min_r,
            maxRadius=max_r,
        )
        if circles is None:
            return None
        circles = np.squeeze(circles, axis=0)
        if circles.ndim == 1:
            circles = circles.reshape(1, -1)
        local = np.array([ix - x0, iy - y0], dtype=np.float64)
        d = np.hypot(circles[:, 0] - local[0], circles[:, 1] - local[1])
        k = int(np.argmin(d))
        cx, cy, r = circles[k, :3]
        label = "circle" if mode == SnapMode.CIRCLE_CENTER else "pad"
        return SnapProposal(
            mode,
            float(cx + x0),
            float(cy + y0),
            float(d[k]),
            f"Proposed {label} center r={r:.1f}px (confirm required)",
        )

    if mode == SnapMode.LINE_INTERSECTION:
        edges = cv2.Canny(roi, 50, 150)
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=20, minLineLength=8, maxLineGap=4)
        if lines is None or len(lines) < 2:
            return None
        best = None
        best_d = 1e9
        segs = [ln[0] for ln in lines]
        from core.geometry import segment_intersection

        for i in range(len(segs)):
            for j in range(i + 1, len(segs)):
                a = segs[i]
                b = segs[j]
                hit = segment_intersection(
                    np.array([a[0], a[1]], dtype=np.float64),
                    np.array([a[2], a[3]], dtype=np.float64),
                    np.array([b[0], b[1]], dtype=np.float64),
                    np.array([b[2], b[3]], dtype=np.float64),
                )
                if hit is None:
                    continue
                d = float(np.hypot(hit[0] - (ix - x0), hit[1] - (iy - y0)))
                if d < best_d:
                    best_d = d
                    best = hit
        if best is None:
            return None
        return SnapProposal(
            SnapMode.LINE_INTERSECTION,
            float(best[0] + x0),
            float(best[1] + y0),
            best_d,
            "Proposed line intersection (confirm required)",
        )
    return None
