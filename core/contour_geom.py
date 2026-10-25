"""Flexible contour geometry: lines, arcs, quadratic/cubic Bezier, node types."""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np

from models.contour import Contour, ContourVertex
from models.enums import Side


NODE_CORNER = "CORNER"
NODE_SMOOTH = "SMOOTH"
NODE_SYMMETRIC = "SYMMETRIC"

SEG_LINE = "line"
SEG_ARC = "arc"
SEG_QUAD = "quadratic"
SEG_CUBIC = "cubic"


def _pt(v: ContourVertex) -> np.ndarray:
    return np.array([v.x, v.y], dtype=np.float64)


def handles(v: ContourVertex) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    c1 = (v.c1x, v.c1y) if v.c1x is not None and v.c1y is not None else None
    c2 = (v.c2x, v.c2y) if v.c2x is not None and v.c2y is not None else None
    return c1, c2


def enforce_node_type(prev: ContourVertex | None, node: ContourVertex, nxt: ContourVertex | None) -> None:
    """Keep SMOOTH / SYMMETRIC handle constraints after a handle or node move."""
    kind = (node.node_type or NODE_CORNER).upper()
    if kind == NODE_CORNER:
        return
    in_h = (node.c1x, node.c1y) if node.c1x is not None and node.c1y is not None else None
    out_h = (node.c2x, node.c2y) if node.c2x is not None and node.c2y is not None else None
    if in_h is None and out_h is None:
        return
    px, py = node.x, node.y
    if in_h is None and out_h is not None:
        vx, vy = out_h[0] - px, out_h[1] - py
        node.c1x = px - vx
        node.c1y = py - vy
        return
    if out_h is None and in_h is not None:
        vx, vy = in_h[0] - px, in_h[1] - py
        node.c2x = px - vx
        node.c2y = py - vy
        return
    vin = np.array([in_h[0] - px, in_h[1] - py], dtype=np.float64)
    vout = np.array([out_h[0] - px, out_h[1] - py], dtype=np.float64)
    lin, lout = float(np.linalg.norm(vin)), float(np.linalg.norm(vout))
    if lin < 1e-12 and lout < 1e-12:
        return
    if kind == NODE_SYMMETRIC:
        if lout >= lin:
            direction = vout / max(lout, 1e-12)
            node.c1x = px - direction[0] * lout
            node.c1y = py - direction[1] * lout
        else:
            direction = vin / max(lin, 1e-12)
            node.c2x = px - direction[0] * lin
            node.c2y = py - direction[1] * lin
        return
    # SMOOTH: collinear, independent lengths
    if lout >= 1e-12:
        direction = vout / lout
        node.c1x = px - direction[0] * (lin if lin else lout)
        node.c1y = py - direction[1] * (lin if lin else lout)
    elif lin >= 1e-12:
        direction = vin / lin
        node.c2x = px - direction[0] * lout if lout else px - direction[0] * lin
        node.c2y = py - direction[1] * lout if lout else py - direction[1] * lin


def _cubic_points(p0, c1, c2, p3, n: int = 24) -> list[tuple[float, float]]:
    ts = np.linspace(0.0, 1.0, n)
    out = []
    for t in ts:
        u = 1.0 - t
        p = (u**3) * p0 + 3 * (u**2) * t * c1 + 3 * u * (t**2) * c2 + (t**3) * p3
        out.append((float(p[0]), float(p[1])))
    return out


def _quad_points(p0, c1, p2, n: int = 20) -> list[tuple[float, float]]:
    ts = np.linspace(0.0, 1.0, n)
    out = []
    for t in ts:
        u = 1.0 - t
        p = (u**2) * p0 + 2 * u * t * c1 + (t**2) * p2
        out.append((float(p[0]), float(p[1])))
    return out


def _arc_points(p0, mid, p1, n: int = 20) -> list[tuple[float, float]]:
    """Circular arc through p0, mid (or implied via handle), p1."""
    a, b, c = np.asarray(p0, float), np.asarray(mid, float), np.asarray(p1, float)
    d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) < 1e-9:
        return [(float(a[0]), float(a[1])), (float(c[0]), float(c[1]))]
    ux = ((a[0] ** 2 + a[1] ** 2) * (b[1] - c[1]) + (b[0] ** 2 + b[1] ** 2) * (c[1] - a[1]) + (c[0] ** 2 + c[1] ** 2) * (a[1] - b[1])) / d
    uy = ((a[0] ** 2 + a[1] ** 2) * (c[0] - b[0]) + (b[0] ** 2 + b[1] ** 2) * (a[0] - c[0]) + (c[0] ** 2 + c[1] ** 2) * (b[0] - a[0])) / d
    center = np.array([ux, uy])
    r0 = a - center
    r1 = c - center
    ang0 = math.atan2(r0[1], r0[0])
    ang1 = math.atan2(r1[1], r1[0])
    mid_ang = math.atan2((b - center)[1], (b - center)[0])
    # Pick the sweep that passes near mid
    def sweep(a0, a1):
        dlt = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
        return dlt

    dlt = sweep(ang0, ang1)
    trial = ang0 + dlt * 0.5
    alt = sweep(ang0, ang1 + 2 * math.pi * (-1 if dlt > 0 else 1))
    if abs(((mid_ang - trial + math.pi) % (2 * math.pi)) - math.pi) > abs(
        ((mid_ang - (ang0 + alt * 0.5) + math.pi) % (2 * math.pi)) - math.pi
    ):
        dlt = alt
    radius = float(np.linalg.norm(r0))
    out = []
    for i in range(n):
        t = i / (n - 1)
        ang = ang0 + dlt * t
        out.append((float(center[0] + radius * math.cos(ang)), float(center[1] + radius * math.sin(ang))))
    return out


def sample_contour(contour: Contour, *, density: int = 24) -> np.ndarray:
    verts = contour.vertices
    if len(verts) < 2:
        return contour.points_array()
    pts: list[tuple[float, float]] = []
    n = len(verts)
    count = n if contour.closed else n - 1
    for i in range(count):
        a = verts[i]
        b = verts[(i + 1) % n]
        kind = (a.kind or SEG_LINE).lower()
        p0 = _pt(a)
        p1 = _pt(b)
        if kind == SEG_CUBIC:
            c1 = np.array([a.c2x if a.c2x is not None else a.x, a.c2y if a.c2y is not None else a.y], dtype=np.float64)
            c2 = np.array([b.c1x if b.c1x is not None else b.x, b.c1y if b.c1y is not None else b.y], dtype=np.float64)
            chunk = _cubic_points(p0, c1, c2, p1, density)
        elif kind == SEG_QUAD or kind == "bezier":
            ctrl = np.array(
                [a.c2x if a.c2x is not None else (a.x + b.x) / 2.0, a.c2y if a.c2y is not None else (a.y + b.y) / 2.0],
                dtype=np.float64,
            )
            chunk = _quad_points(p0, ctrl, p1, density)
        elif kind == SEG_ARC:
            mid = (
                a.c2x if a.c2x is not None else (a.x + b.x) / 2.0,
                a.c2y if a.c2y is not None else (a.y + b.y) / 2.0,
            )
            chunk = _arc_points(p0, mid, p1, density)
        else:
            chunk = [(float(p0[0]), float(p0[1])), (float(p1[0]), float(p1[1]))]
        if pts and chunk:
            chunk = chunk[1:]
        pts.extend(chunk)
    if not pts:
        return np.zeros((0, 2), dtype=np.float64)
    return np.asarray(pts, dtype=np.float64)


def transform_vertex(v: ContourVertex, fn) -> ContourVertex:
    x, y = fn(v.x, v.y)
    c1x = c1y = c2x = c2y = None
    if v.c1x is not None and v.c1y is not None:
        c1x, c1y = fn(v.c1x, v.c1y)
    if v.c2x is not None and v.c2y is not None:
        c2x, c2y = fn(v.c2x, v.c2y)
    return ContourVertex(
        x=x,
        y=y,
        confidence=v.confidence,
        kind=v.kind,
        notes=v.notes,
        c1x=c1x,
        c1y=c1y,
        c2x=c2x,
        c2y=c2y,
        node_type=v.node_type,
    )


def project_contour(contour: Contour, fn, *, new_id: str, side: Side, label: str, source: str = "projected") -> Contour:
    return Contour(
        id=new_id,
        side=side,
        label=label,
        vertices=[transform_vertex(v, fn) for v in contour.vertices],
        closed=contour.closed,
        group=contour.group,
        notes=contour.notes,
        source=source,
        accepted_proposal=contour.accepted_proposal,
        role="EXPECTED",
        pair_id=contour.id,
    )


def contour_deviation(expected: np.ndarray, actual: np.ndarray) -> dict:
    """Point-to-polyline stats: mean / median / RMSE / max of actual vs expected."""
    from core.geometry import point_to_polyline_distance

    if expected.shape[0] < 2 or actual.shape[0] < 1:
        return {"mean": None, "median": None, "rmse": None, "max": None, "samples": []}
    dists = []
    for p in actual:
        d, _closest, _seg = point_to_polyline_distance(p, expected)
        dists.append(abs(d))
    arr = np.asarray(dists, dtype=np.float64)
    return {
        "mean": float(arr.mean()),
        "median": float(np.median(arr)),
        "rmse": float(np.sqrt(np.mean(arr**2))),
        "max": float(arr.max()),
        "samples": dists,
    }


def reverse_contour(contour: Contour) -> None:
    contour.vertices = list(reversed(contour.vertices))
    for v in contour.vertices:
        v.c1x, v.c2x = v.c2x, v.c1x
        v.c1y, v.c2y = v.c2y, v.c1y
