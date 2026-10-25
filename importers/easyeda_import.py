"""EasyEDA importer.

Supports:
1. Native EasyEDA standard / pro JSON exports (shape-string format).
2. Intermediate JSON documented below (for SVG/DXF/JSON workflows).

Board Outline is taken from outline/solid-region geometry only.
Component bounding boxes are NEVER used as the board outline.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from models.enums import LayerName, Side
from models.vector import VectorDocument, VectorPrimitive


class EasyEDAImportError(ValueError):
    pass


# Intermediate schema (expandable):
# {
#   "format": "easyeda-intermediate",
#   "unit": "mm",
#   "board_outline": [{"x":0,"y":0}, ...],
#   "pads": [{"x":..,"y":..,"width":..,"height":..,"rotation":0}],
#   "vias": [{"x":..,"y":..,"d":..}],
#   "holes": [{"x":..,"y":..,"d":..}],
#   "slots": [{"points":[[x,y],...]}],
#   "tracks": [{"points":[[x,y],...],"width":0.2}],
#   "components": [{"ref":"U1","x":..,"y":..}]
# }


def load_easyeda(path: str | Path, side: Side = Side.B) -> VectorDocument:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EasyEDAImportError(f"EasyEDA file is not valid JSON: {exc}") from exc
    if isinstance(data, dict) and data.get("format") == "easyeda-intermediate":
        return _from_intermediate(data, path, side)
    if isinstance(data, dict) and ("shape" in data or "canvas" in data or data.get("docType") is not None):
        return _from_native(data, path, side)
    raise EasyEDAImportError(
        "Unrecognized EasyEDA file. Export the board as EasyEDA JSON, "
        "or use the intermediate JSON schema (format=easyeda-intermediate)."
    )


def _from_intermediate(data: dict, path: Path, side: Side) -> VectorDocument:
    unit = data.get("unit", "mm")
    prims: list[VectorPrimitive] = []
    outline = data.get("board_outline") or []
    if outline:
        pts = [(float(p["x"]), float(p["y"])) if isinstance(p, dict) else (float(p[0]), float(p[1])) for p in outline]
        prims.append(VectorPrimitive(kind="polyline", layer=LayerName.BOARD_OUTLINE, points=pts, closed=True, unit=unit))
    for pad in data.get("pads") or []:
        prims.append(
            VectorPrimitive(
                kind="pad",
                layer=LayerName.PADS,
                points=[(float(pad["x"]), float(pad["y"]))],
                width=pad.get("width"),
                attributes={k: pad[k] for k in pad if k not in ("x", "y")},
                unit=unit,
            )
        )
    for via in data.get("vias") or []:
        prims.append(
            VectorPrimitive(
                kind="via",
                layer=LayerName.VIAS,
                points=[(float(via["x"]), float(via["y"]))],
                radius=float(via.get("d", 0)) / 2.0,
                unit=unit,
            )
        )
    for hole in data.get("holes") or []:
        prims.append(
            VectorPrimitive(
                kind="hole",
                layer=LayerName.HOLES,
                points=[(float(hole["x"]), float(hole["y"]))],
                radius=float(hole.get("d", 0)) / 2.0,
                unit=unit,
            )
        )
    for slot in data.get("slots") or []:
        pts = [(float(p[0]), float(p[1])) for p in slot.get("points") or []]
        prims.append(VectorPrimitive(kind="slot", layer=LayerName.SLOTS, points=pts, unit=unit))
    for tr in data.get("tracks") or []:
        pts = [(float(p[0]), float(p[1])) for p in tr.get("points") or []]
        prims.append(
            VectorPrimitive(kind="track", layer=LayerName.TRACKS, points=pts, width=tr.get("width"), unit=unit)
        )
    for comp in data.get("components") or []:
        prims.append(
            VectorPrimitive(
                kind="component",
                layer=LayerName.COMPONENTS,
                points=[(float(comp.get("x", 0)), float(comp.get("y", 0)))],
                attributes={"ref": comp.get("ref", "")},
                unit=unit,
            )
        )
    return VectorDocument(
        source=str(path),
        side=side,
        unit=unit,
        primitives=prims,
        notes="EasyEDA intermediate JSON. Component bboxes were not used as Board Outline.",
    )


def _from_native(data: dict, path: Path, side: Side) -> VectorDocument:
    """Parse EasyEDA standard editor `shape` string list.

    Shape tokens are `~` separated. This covers the geometry needed for
    outline / pads / vias / holes / tracks. The parser is structured so
    additional native records can be added without changing the document model.
    """
    shapes = data.get("shape") or []
    prims: list[VectorPrimitive] = []
    unit = "mm"
    for raw in shapes:
        if not isinstance(raw, str) or "~" not in raw:
            continue
        parts = raw.split("~")
        kind = parts[0].upper()
        try:
            prims.extend(_parse_shape(kind, parts))
        except Exception:
            continue
    has_outline = any(p.layer == LayerName.BOARD_OUTLINE for p in prims)
    note = "Native EasyEDA JSON. "
    if not has_outline:
        note += "No Board Outline geometry found — component bbox was not substituted. "
    note += f"{len(prims)} primitives extracted."
    return VectorDocument(source=str(path), side=side, unit=unit, primitives=prims, notes=note)


def _parse_shape(kind: str, parts: list[str]) -> list[VectorPrimitive]:
    out: list[VectorPrimitive] = []
    if kind == "TRACK":
        # TRACK~width~layer~net~points~id~...
        width = _f(parts, 1)
        layer = _easy_layer(parts[2] if len(parts) > 2 else "")
        pts = _points(parts[4] if len(parts) > 4 else "")
        out.append(VectorPrimitive(kind="track", layer=layer, points=pts, width=width, unit="mm"))
    elif kind == "PAD":
        # several historic layouts; try to find x,y
        # PAD~shape~cx~cy~...
        if len(parts) > 3:
            x, y = _f(parts, 2), _f(parts, 3)
            out.append(
                VectorPrimitive(
                    kind="pad",
                    layer=LayerName.PADS,
                    points=[(x, y)],
                    attributes={"raw_shape": parts[1] if len(parts) > 1 else ""},
                    unit="mm",
                )
            )
    elif kind == "VIA":
        if len(parts) > 2:
            out.append(
                VectorPrimitive(
                    kind="via",
                    layer=LayerName.VIAS,
                    points=[(_f(parts, 1), _f(parts, 2))],
                    radius=_f(parts, 3) / 2.0 if len(parts) > 3 else None,
                    unit="mm",
                )
            )
    elif kind == "HOLE":
        if len(parts) > 2:
            out.append(
                VectorPrimitive(
                    kind="hole",
                    layer=LayerName.HOLES,
                    points=[(_f(parts, 1), _f(parts, 2))],
                    radius=_f(parts, 3) / 2.0 if len(parts) > 3 else None,
                    unit="mm",
                )
            )
    elif kind in ("SOLIDREGION", "COPPERAREA"):
        pts = _points(parts[3] if len(parts) > 3 else "")
        layer_id = parts[1] if len(parts) > 1 else ""
        layer = LayerName.BOARD_OUTLINE if layer_id in ("10", "outline", "BOARD_OUTLINE") else _easy_layer(layer_id)
        # EasyEDA layer 10 is often board outline
        if layer_id == "10":
            layer = LayerName.BOARD_OUTLINE
        out.append(VectorPrimitive(kind="polyline", layer=layer, points=pts, closed=True, unit="mm"))
    elif kind == "CIRCLE":
        if len(parts) > 3:
            out.append(
                VectorPrimitive(
                    kind="circle",
                    layer=_easy_layer(parts[1] if len(parts) > 1 else ""),
                    points=[(_f(parts, 2), _f(parts, 3))],
                    radius=_f(parts, 4) if len(parts) > 4 else None,
                    unit="mm",
                )
            )
    elif kind == "RECT":
        if len(parts) > 5:
            x, y, w, h = _f(parts, 2), _f(parts, 3), _f(parts, 4), _f(parts, 5)
            pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
            out.append(VectorPrimitive(kind="rect", layer=_easy_layer(parts[1]), points=pts, closed=True, unit="mm"))
    return out


def _f(parts: list[str], i: int) -> float:
    try:
        return float(parts[i])
    except (IndexError, ValueError):
        return 0.0


def _points(text: str) -> list[tuple[float, float]]:
    nums = []
    for tok in text.replace(" ", " ").replace("M", " ").replace("L", " ").split():
        try:
            nums.append(float(tok))
        except ValueError:
            continue
    # also try space-separated x,y pairs with commas
    if not nums:
        for pair in text.replace(" ", ",").split(","):
            pass
        raw = [p for p in text.replace(",", " ").split() if p]
        try:
            nums = [float(p) for p in raw]
        except ValueError:
            nums = []
    return [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]


def _easy_layer(code: str) -> LayerName:
    mapping = {
        "1": LayerName.TRACKS,  # top copper
        "2": LayerName.TRACKS,  # bottom copper
        "3": LayerName.SILKSCREEN,
        "10": LayerName.BOARD_OUTLINE,
        "11": LayerName.HOLES,
        "12": LayerName.PADS,
    }
    return mapping.get(str(code), LayerName.OTHER)
