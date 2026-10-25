"""CSV point and contour import. Units must be explicit in headers or metadata."""

from __future__ import annotations

import csv
from pathlib import Path

from models.contour import Contour, ContourVertex
from models.enums import Confidence, PointType, Side
from models.landmark import Landmark


class CsvImportError(ValueError):
    pass


def _norm(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def load_points_csv(path: str | Path, *, default_type: PointType = PointType.MEASUREMENT) -> list[Landmark]:
    path = Path(path)
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise CsvImportError("CSV has no header row.")
        fields = {_norm(f): f for f in reader.fieldnames}
        landmarks = []
        for i, row in enumerate(reader, start=1):
            def get(*names):
                for n in names:
                    if n in fields:
                        val = row.get(fields[n], "").strip()
                        if val != "":
                            return val
                return None

            pid = get("id", "point_id", "name") or f"CSV_{i:03d}"
            ptype = get("type", "point_type")
            try:
                pt = PointType(ptype) if ptype else default_type
            except ValueError:
                pt = default_type
            ax = get("a_x", "a_x_px", "x_a", "x")
            ay = get("a_y", "a_y_px", "y_a", "y")
            bx = get("b_x", "b_actual_x", "x_b")
            by = get("b_y", "b_actual_y", "y_b")
            landmarks.append(
                Landmark(
                    id=pid,
                    point_type=pt,
                    label=get("label") or pid,
                    group=get("group") or "",
                    a_x=float(ax) if ax is not None else None,
                    a_y=float(ay) if ay is not None else None,
                    b_x=float(bx) if bx is not None else None,
                    b_y=float(by) if by is not None else None,
                    notes=get("notes") or "",
                    confidence=Confidence(get("confidence") or Confidence.UNVERIFIED.value)
                    if get("confidence")
                    else Confidence.UNVERIFIED,
                )
            )
        return landmarks


def load_contour_csv(path: str | Path, side: Side, contour_id: str, *, closed: bool = False) -> Contour:
    path = Path(path)
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise CsvImportError("CSV has no header row.")
        fields = {_norm(f): f for f in reader.fieldnames}
        verts: list[ContourVertex] = []
        for row in reader:
            def get(*names):
                for n in names:
                    if n in fields:
                        val = row.get(fields[n], "").strip()
                        if val != "":
                            return val
                return None

            x = get("x", "x_px", "a_x")
            y = get("y", "y_px", "a_y")
            if x is None or y is None:
                continue
            conf = get("confidence") or Confidence.UNVERIFIED.value
            verts.append(ContourVertex(x=float(x), y=float(y), confidence=Confidence(conf)))
    if len(verts) < 2:
        raise CsvImportError("Contour CSV must contain at least 2 points.")
    return Contour(
        id=contour_id,
        side=side,
        label=path.stem,
        vertices=verts,
        closed=closed,
        source="csv",
    )
