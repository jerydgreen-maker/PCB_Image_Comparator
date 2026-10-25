"""Automatic edge proposal. Never classified as a physical boundary until accepted."""

from __future__ import annotations

import numpy as np

from models.contour import Contour, ContourVertex
from models.enums import Confidence, Side


def propose_largest_edge(image: np.ndarray, side: Side, contour_id: str) -> Contour:
    import cv2

    if image.ndim == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image
    edges = cv2.Canny(gray, 60, 180)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise ValueError("No edge proposal could be generated from this image.")
    best = max(contours, key=cv2.contourArea)
    if len(best) > 400:
        step = max(1, len(best) // 200)
        best = best[::step]
    verts = [
        ContourVertex(x=float(p[0][0]), y=float(p[0][1]), confidence=Confidence.UNVERIFIED)
        for p in best
    ]
    if len(verts) < 3:
        raise ValueError("Edge proposal was too short to use.")
    return Contour(
        id=contour_id,
        side=side,
        label=f"PROPOSED edge {side.value}",
        vertices=verts,
        closed=True,
        source="proposal",
        accepted_proposal=False,
        notes="Automatic Canny proposal — not a physical PCB boundary until the user accepts it.",
    )
