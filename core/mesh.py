"""Delaunay landmark mesh and local geometry comparison.

Topology is computed once and applied to both sides.
CONTROL points never change registration.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from models.mesh import LandmarkMesh, MeshEdge


@dataclass
class TriangleMetrics:
    ids: tuple[str, str, str]
    area_a: float | None
    area_b: float | None
    area_delta: float | None
    local_scale: float | None
    local_rotation_deg: float | None
    shape_error: float | None
    edges: list[tuple[str, str, float | None, float | None]]

    def as_dict(self) -> dict:
        return {
            "P1": self.ids[0],
            "P2": self.ids[1],
            "P3": self.ids[2],
            "AREA_A": self.area_a,
            "AREA_B": self.area_b,
            "AREA_DELTA": self.area_delta,
            "LOCAL_SCALE": self.local_scale,
            "LOCAL_ROTATION": self.local_rotation_deg,
            "SHAPE_ERROR": self.shape_error,
        }


def _triangle_area(p: np.ndarray) -> float:
    a, b, c = p
    return 0.5 * abs(float((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])))


def _orientation(p: np.ndarray) -> float:
    v = p[1] - p[0]
    return math.degrees(math.atan2(float(v[1]), float(v[0])))


def delaunay_edges(ids: list[str], points: np.ndarray) -> list[tuple[str, str]]:
    """Unique undirected edges from a Delaunay triangulation. Identical IDs → identical topology."""
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    if len(ids) != len(pts):
        raise ValueError("IDs and points must match.")
    if len(ids) < 2:
        return []
    if len(ids) == 2:
        return [tuple(sorted((ids[0], ids[1])))]
    try:
        from scipy.spatial import Delaunay
    except ImportError as exc:
        raise RuntimeError("SciPy is required to build a Delaunay mesh.") from exc
    # Degenerate / near-colinear: fall back to nearest-neighbour chain
    if np.linalg.matrix_rank(pts - pts.mean(0), tol=1e-9) < 2:
        order = np.argsort(pts[:, 0] + pts[:, 1] * 0.01)
        return [tuple(sorted((ids[order[i]], ids[order[i + 1]]))) for i in range(len(order) - 1)]
    tri = Delaunay(pts)
    edges: set[tuple[str, str]] = set()
    for simplex in tri.simplices:
        for i, j in ((0, 1), (1, 2), (2, 0)):
            edges.add(tuple(sorted((ids[int(simplex[i])], ids[int(simplex[j])]))))
    return sorted(edges)


def rebuild_automatic(mesh: LandmarkMesh, ids: list[str], points: np.ndarray) -> LandmarkMesh:
    """Keep locked/manual edges; replace automatic edges from Delaunay of `points`."""
    kept = [e for e in mesh.edges if e.manual or e.locked]
    kept_pairs = {e.pair() for e in kept}
    auto = delaunay_edges(ids, points)
    for a, b in auto:
        if (a, b) not in kept_pairs:
            kept.append(MeshEdge(a=a, b=b, manual=False, locked=False))
    mesh.edges = kept
    return mesh


def triangles_from_edges(ids: list[str], points: np.ndarray, edges: list[MeshEdge]) -> list[tuple[str, str, str]]:
    pts = {i: p for i, p in zip(ids, np.asarray(points, dtype=np.float64).reshape(-1, 2))}
    adj: dict[str, set[str]] = {i: set() for i in ids}
    for e in edges:
        if e.a in adj and e.b in adj:
            adj[e.a].add(e.b)
            adj[e.b].add(e.a)
    found: set[tuple[str, str, str]] = set()
    for a in ids:
        for b in adj[a]:
            for c in adj[a] & adj[b]:
                found.add(tuple(sorted((a, b, c))))
    # Keep only non-colinear triangles
    out = []
    for trip in sorted(found):
        p = np.array([pts[trip[0]], pts[trip[1]], pts[trip[2]]])
        if _triangle_area(p) > 1e-8:
            out.append(trip)
    return out


def analyze_triangle(
    ids: tuple[str, str, str],
    pts_a: dict[str, np.ndarray],
    pts_b: dict[str, np.ndarray],
) -> TriangleMetrics:
    pa = np.array([pts_a[i] for i in ids], dtype=np.float64)
    pb = np.array([pts_b[i] for i in ids], dtype=np.float64) if all(i in pts_b for i in ids) else None
    area_a = _triangle_area(pa)
    area_b = _triangle_area(pb) if pb is not None else None
    scale = (area_b / area_a) ** 0.5 if area_a and area_b is not None else None
    rot = None
    shape = None
    if pb is not None:
        rot = _orientation(pb) - _orientation(pa)
        # shape residual: edge-length ratios minus local scale
        ea = [float(np.linalg.norm(pa[(i + 1) % 3] - pa[i])) for i in range(3)]
        eb = [float(np.linalg.norm(pb[(i + 1) % 3] - pb[i])) for i in range(3)]
        if scale and scale > 0:
            shape = float(np.sqrt(np.mean([(eb[i] / max(ea[i], 1e-12) - scale) ** 2 for i in range(3)])))
    edges = []
    for i in range(3):
        u, v = ids[i], ids[(i + 1) % 3]
        la = float(np.linalg.norm(pts_a[v] - pts_a[u])) if u in pts_a and v in pts_a else None
        lb = float(np.linalg.norm(pts_b[v] - pts_b[u])) if pb is not None else None
        edges.append((u, v, la, lb))
    return TriangleMetrics(
        ids=ids,
        area_a=area_a,
        area_b=area_b,
        area_delta=None if area_b is None else area_b - area_a,
        local_scale=scale,
        local_rotation_deg=rot,
        shape_error=shape,
        edges=edges,
    )


def edge_stats(id_a: str, id_b: str, pts_a: dict, pts_b: dict) -> dict:
    la = lb = None
    if id_a in pts_a and id_b in pts_a:
        la = float(np.linalg.norm(pts_a[id_b] - pts_a[id_a]))
    if id_a in pts_b and id_b in pts_b:
        lb = float(np.linalg.norm(pts_b[id_b] - pts_b[id_a]))
    delta = None if la is None or lb is None else lb - la
    pct = None if la is None or la == 0 or delta is None else 100.0 * delta / la
    return {
        "POINT_A": id_a,
        "POINT_B": id_b,
        "LENGTH_A": la,
        "LENGTH_B": lb,
        "DELTA": delta,
        "PERCENT_DELTA": pct,
    }
