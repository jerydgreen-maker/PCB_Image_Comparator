"""Pure 2D geometry helpers. All coordinates are float (subpixel)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Point2D:
    x: float
    y: float

    def as_tuple(self) -> tuple[float, float]:
        return (float(self.x), float(self.y))

    def as_array(self) -> np.ndarray:
        return np.array([self.x, self.y], dtype=np.float64)

    def distance_to(self, other: "Point2D") -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def __add__(self, other: "Point2D") -> "Point2D":
        return Point2D(self.x + other.x, self.y + other.y)

    def __sub__(self, other: "Point2D") -> "Point2D":
        return Point2D(self.x - other.x, self.y - other.y)

    def scaled(self, s: float) -> "Point2D":
        return Point2D(self.x * s, self.y * s)


def as_points(coords: np.ndarray | list | tuple) -> np.ndarray:
    arr = np.asarray(coords, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr.reshape(1, 2)
    if arr.shape[-1] != 2:
        raise ValueError("Coordinates must be Nx2.")
    return arr.reshape(-1, 2)


def pairwise_distances(points: np.ndarray) -> np.ndarray:
    pts = as_points(points)
    n = len(pts)
    dist = np.zeros((n, n), dtype=np.float64)
    for i in range(n):
        diff = pts - pts[i]
        dist[i] = np.hypot(diff[:, 0], diff[:, 1])
    return dist


def nearest_neighbor_distances(points: np.ndarray) -> np.ndarray:
    dist = pairwise_distances(points)
    n = dist.shape[0]
    if n < 2:
        return np.array([], dtype=np.float64)
    np.fill_diagonal(dist, np.inf)
    return dist.min(axis=1)


def centroid(points: np.ndarray) -> np.ndarray:
    pts = as_points(points)
    if len(pts) == 0:
        raise ValueError("Cannot compute centroid of an empty set.")
    return pts.mean(axis=0)


def span_xy(points: np.ndarray) -> tuple[float, float]:
    pts = as_points(points)
    if len(pts) == 0:
        return 0.0, 0.0
    return float(pts[:, 0].max() - pts[:, 0].min()), float(pts[:, 1].max() - pts[:, 1].min())


def principal_orientation_deg(points: np.ndarray) -> float:
    pts = as_points(points)
    if len(pts) < 2:
        return 0.0
    centered = pts - pts.mean(axis=0)
    cov = centered.T @ centered
    eigvals, eigvecs = np.linalg.eigh(cov)
    axis = eigvecs[:, int(np.argmax(eigvals))]
    return float(math.degrees(math.atan2(axis[1], axis[0])))


def angle_deg(p0: Point2D, p1: Point2D) -> float:
    return math.degrees(math.atan2(p1.y - p0.y, p1.x - p0.x))


def three_point_angle_deg(p0: Point2D, vertex: Point2D, p2: Point2D) -> float:
    v1 = np.array([p0.x - vertex.x, p0.y - vertex.y], dtype=np.float64)
    v2 = np.array([p2.x - vertex.x, p2.y - vertex.y], dtype=np.float64)
    n1 = np.linalg.norm(v1)
    n2 = np.linalg.norm(v2)
    if n1 == 0 or n2 == 0:
        raise ValueError("Angle points must be distinct.")
    cos = float(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0))
    return math.degrees(math.acos(cos))


def segment_intersection(
    a1: np.ndarray, a2: np.ndarray, b1: np.ndarray, b2: np.ndarray, *, tol: float = 1e-9
) -> np.ndarray | None:
    """Intersection of two finite segments, or None."""
    r = a2 - a1
    s = b2 - b1
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) < tol:
        return None
    qp = b1 - a1
    t = (qp[0] * s[1] - qp[1] * s[0]) / den
    u = (qp[0] * r[1] - qp[1] * r[0]) / den
    if -tol <= t <= 1 + tol and -tol <= u <= 1 + tol:
        return a1 + t * r
    return None


def line_polyline_intersections(
    origin: np.ndarray, direction: np.ndarray, polyline: np.ndarray, *, closed: bool = False
) -> list[np.ndarray]:
    """Intersections of an infinite line with a polyline."""
    pts = as_points(polyline)
    if len(pts) < 2:
        return []
    segs = list(zip(pts[:-1], pts[1:]))
    if closed and len(pts) >= 3:
        segs.append((pts[-1], pts[0]))
    # Represent infinite line as a very long segment
    scale = 1e6
    a1 = origin - direction * scale
    a2 = origin + direction * scale
    hits: list[np.ndarray] = []
    for b1, b2 in segs:
        hit = segment_intersection(a1, a2, b1, b2)
        if hit is not None:
            hits.append(hit)
    return hits


def point_to_polyline_distance(
    point: np.ndarray, polyline: np.ndarray, *, closed: bool = False
) -> tuple[float, np.ndarray, int]:
    """Return (distance, closest_point, segment_index)."""
    pts = as_points(polyline)
    p = np.asarray(point, dtype=np.float64)
    if len(pts) == 0:
        raise ValueError("Empty polyline.")
    if len(pts) == 1:
        return float(np.linalg.norm(p - pts[0])), pts[0].copy(), 0
    segs = list(range(len(pts) - 1))
    if closed and len(pts) >= 3:
        segs.append(len(pts) - 1)
    best_d = float("inf")
    best_pt = pts[0]
    best_i = 0
    for i in segs:
        a = pts[i]
        b = pts[(i + 1) % len(pts)]
        ab = b - a
        length2 = float(np.dot(ab, ab))
        if length2 == 0:
            proj = a
        else:
            t = float(np.clip(np.dot(p - a, ab) / length2, 0.0, 1.0))
            proj = a + t * ab
        d = float(np.linalg.norm(p - proj))
        if d < best_d:
            best_d = d
            best_pt = proj
            best_i = i
    return best_d, best_pt, best_i


def polyline_normals(polyline: np.ndarray, *, closed: bool = False) -> np.ndarray:
    """Outward-ish left-hand normals for each vertex (for signed deviation)."""
    pts = as_points(polyline)
    n = len(pts)
    normals = np.zeros((n, 2), dtype=np.float64)
    if n < 2:
        return normals
    for i in range(n):
        if closed:
            prev = pts[(i - 1) % n]
            nxt = pts[(i + 1) % n]
        else:
            prev = pts[max(0, i - 1)]
            nxt = pts[min(n - 1, i + 1)]
        t = nxt - prev
        length = np.linalg.norm(t)
        if length == 0:
            continue
        t = t / length
        normals[i] = np.array([-t[1], t[0]])
    if closed and n >= 3:
        # Flip so average normal points outward (away from centroid)
        c = pts.mean(axis=0)
        outward = 0.0
        for i, p in enumerate(pts):
            outward += float(np.dot(normals[i], p - c))
        if outward < 0:
            normals = -normals
    return normals


def resample_polyline(polyline: np.ndarray, count: int, *, closed: bool = False) -> np.ndarray:
    pts = as_points(polyline)
    if len(pts) == 0 or count <= 0:
        return np.zeros((0, 2), dtype=np.float64)
    if len(pts) == 1:
        return np.repeat(pts, count, axis=0)
    work = pts
    if closed and not np.allclose(pts[0], pts[-1]):
        work = np.vstack([pts, pts[0]])
    seglen = np.sqrt(((work[1:] - work[:-1]) ** 2).sum(axis=1))
    total = float(seglen.sum())
    if total == 0:
        return np.repeat(work[:1], count, axis=0)
    cum = np.concatenate([[0.0], np.cumsum(seglen)])
    targets = np.linspace(0.0, total, count, endpoint=not closed)
    out = np.zeros((len(targets), 2), dtype=np.float64)
    j = 0
    for i, t in enumerate(targets):
        while j < len(seglen) - 1 and cum[j + 1] < t:
            j += 1
        span = seglen[j]
        u = 0.0 if span == 0 else (t - cum[j]) / span
        out[i] = work[j] + u * (work[j + 1] - work[j])
    return out
