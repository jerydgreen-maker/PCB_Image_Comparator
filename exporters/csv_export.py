"""CSV export with explicit units in headers."""

from __future__ import annotations

import csv
import math
from pathlib import Path

from models.enums import PointType, Unit
from models.units import px_to_unit


def _unit_header(name: str, unit: Unit) -> str:
    return f"{name}_{unit.value.upper()}"


def export_points(path: str | Path, project, unit: Unit = Unit.MM) -> None:
    path = Path(path)
    cal_a = project.image_a.calibration.mm_per_px if project.image_a else None
    cal_b = project.image_b.calibration.mm_per_px if project.image_b else None
    xf = project.current_transform.transform if project.current_transform else None
    fields = [
        "ID",
        "TYPE",
        "GROUP",
        "A_X_PX",
        "A_Y_PX",
        _unit_header("A_X", unit),
        _unit_header("A_Y", unit),
        _unit_header("B_PRED_X", unit),
        _unit_header("B_PRED_Y", unit),
        "B_ACTUAL_X_PX",
        "B_ACTUAL_Y_PX",
        _unit_header("B_ACTUAL_X", unit),
        _unit_header("B_ACTUAL_Y", unit),
        _unit_header("DX", unit),
        _unit_header("DY", unit),
        _unit_header("ERROR", unit),
        "ANGLE",
        "CONFIDENCE",
        "STATUS",
        "ENABLED",
        "NOTES",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for lm in project.landmarks:
            pred_x = pred_y = None
            if xf and lm.has_a:
                pred_x, pred_y = xf.apply_one(lm.a_x, lm.a_y)
            dx = dy = err = ang = None
            if pred_x is not None and lm.has_b:
                dx = lm.b_x - pred_x
                dy = lm.b_y - pred_y
                err = (dx**2 + dy**2) ** 0.5
                ang = math.degrees(math.atan2(dy, dx)) if err else 0.0
            w.writerow(
                {
                    "ID": lm.id,
                    "TYPE": lm.point_type.value,
                    "GROUP": lm.group,
                    "A_X_PX": lm.a_x,
                    "A_Y_PX": lm.a_y,
                    _unit_header("A_X", unit): px_to_unit(lm.a_x, cal_a, unit) if lm.has_a else None,
                    _unit_header("A_Y", unit): px_to_unit(lm.a_y, cal_a, unit) if lm.has_a else None,
                    _unit_header("B_PRED_X", unit): px_to_unit(pred_x, cal_b, unit) if pred_x is not None else None,
                    _unit_header("B_PRED_Y", unit): px_to_unit(pred_y, cal_b, unit) if pred_y is not None else None,
                    "B_ACTUAL_X_PX": lm.b_x,
                    "B_ACTUAL_Y_PX": lm.b_y,
                    _unit_header("B_ACTUAL_X", unit): px_to_unit(lm.b_x, cal_b, unit) if lm.has_b else None,
                    _unit_header("B_ACTUAL_Y", unit): px_to_unit(lm.b_y, cal_b, unit) if lm.has_b else None,
                    _unit_header("DX", unit): px_to_unit(dx, cal_b, unit) if dx is not None else None,
                    _unit_header("DY", unit): px_to_unit(dy, cal_b, unit) if dy is not None else None,
                    _unit_header("ERROR", unit): px_to_unit(err, cal_b, unit) if err is not None else None,
                    "ANGLE": ang,
                    "CONFIDENCE": lm.confidence.value,
                    "STATUS": "MEASURED" if lm.has_a and lm.has_b else "EXPECTED" if lm.has_a else "UNVERIFIED",
                    "ENABLED": lm.enabled,
                    "NOTES": lm.notes,
                }
            )


def export_rows(path: str | Path, fieldnames: list[str], rows: list[dict], *, metadata: dict | None = None) -> None:
    path = Path(path)
    with path.open("w", encoding="utf-8", newline="") as fh:
        if metadata:
            fh.write("# PCB Image Comparator CSV\n")
            for k, v in metadata.items():
                fh.write(f"# {k}: {v}\n")
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow(row)


def export_contours(path: str | Path, project, unit: Unit = Unit.MM) -> None:
    rows = []
    for c in project.contours:
        for i, v in enumerate(c.vertices):
            rows.append(
                {
                    "CONTOUR_ID": c.id,
                    "SIDE": c.side.value,
                    "INDEX": i,
                    "X_PX": v.x,
                    "Y_PX": v.y,
                    "CONFIDENCE": v.confidence.value,
                    "CLOSED": c.closed,
                    "SOURCE": c.source,
                    "UNIT_NOTE": unit.value,
                }
            )
    if not rows:
        raise ValueError("No contours to export.")
    export_rows(
        path,
        list(rows[0].keys()),
        rows,
        metadata={"content": "contours", "coordinates": "original image pixels"},
    )


def export_calibration(path: str | Path, project) -> None:
    rows = []
    for label, img in (("A", project.image_a), ("B", project.image_b)):
        if img is None:
            continue
        c = img.calibration.as_dict()
        c["image"] = label
        rows.append(c)
    if not rows:
        raise ValueError("No images to export calibration from.")
    fields = sorted({k for r in rows for k in r})
    export_rows(path, fields, rows, metadata={"content": "calibration", "units": "mm_per_px is millimetres per pixel"})


def export_transform(path: str | Path, project) -> None:
    if project.current_transform is None:
        raise ValueError("No accepted or provisional transform to export.")
    rec = project.current_transform.as_dict()
    export_rows(
        path,
        ["key", "value"],
        [{"key": k, "value": rec[k]} for k in rec],
        metadata={"content": "transform", "units": "pixels in oriented image space"},
    )


def export_width_sections(path: str | Path, sections: list, unit: Unit) -> None:
    fields = [
        "POSITION",
        f"A_WIDTH_{unit.value.upper()}",
        f"B_WIDTH_{unit.value.upper()}",
        f"DELTA_{unit.value.upper()}",
        "A_LEFT",
        "A_RIGHT",
        "B_LEFT",
        "B_RIGHT",
        "AXIS",
    ]
    rows = []
    for s in sections:
        d = s.as_dict() if hasattr(s, "as_dict") else s
        rows.append(
            {
                "POSITION": d.get("position"),
                f"A_WIDTH_{unit.value.upper()}": d.get("a_width"),
                f"B_WIDTH_{unit.value.upper()}": d.get("b_width"),
                f"DELTA_{unit.value.upper()}": d.get("delta_width"),
                "A_LEFT": d.get("a_left"),
                "A_RIGHT": d.get("a_right"),
                "B_LEFT": d.get("b_left"),
                "B_RIGHT": d.get("b_right"),
                "AXIS": d.get("axis"),
            }
        )
    export_rows(path, fields, rows, metadata={"content": "width_cross_sections", "unit": unit.value})
