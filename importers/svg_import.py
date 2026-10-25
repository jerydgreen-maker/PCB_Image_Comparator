"""SVG import. Physical units preserved when the file declares them."""

from __future__ import annotations

import math
import re
from pathlib import Path
from xml.etree import ElementTree as ET

from models.enums import LayerName, Side
from models.vector import VectorDocument, VectorPrimitive

SVG_NS = {"svg": "http://www.w3.org/2000/svg"}


class SvgImportError(ValueError):
    pass


_UNIT_MM = {
    "mm": 1.0,
    "cm": 10.0,
    "in": 25.4,
    "inch": 25.4,
    "pt": 25.4 / 72.0,
    "pc": 25.4 / 6.0,
    "px": None,
}


def _parse_length(text: str | None) -> tuple[float | None, str]:
    if not text:
        return None, "px"
    text = text.strip()
    m = re.match(r"^([+-]?\d+(?:\.\d+)?)([a-zA-Z%]*)$", text)
    if not m:
        return None, "px"
    return float(m.group(1)), (m.group(2) or "px")


def _to_mm(value: float, unit: str, px_per_mm: float | None) -> float:
    unit = unit.lower()
    if unit in ("mm",):
        return value
    if unit in _UNIT_MM and _UNIT_MM[unit] is not None:
        return value * _UNIT_MM[unit]
    if px_per_mm:
        return value / px_per_mm
    return value  # leave as user units; documented in vector.unit


def _local(tag: str) -> str:
    return tag.split("}")[-1]


def _floats(text: str) -> list[float]:
    return [float(x) for x in re.findall(r"[+-]?\d+(?:\.\d+)?", text or "")]


def load_svg(path: str | Path, side: Side = Side.B) -> VectorDocument:
    path = Path(path)
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise SvgImportError(f"Invalid SVG: {exc}") from exc
    root = tree.getroot()
    width_v, width_u = _parse_length(root.get("width"))
    # 96 px/in is the CSS default when units are pixels
    px_per_mm = 96.0 / 25.4
    unit = "mm" if width_u in _UNIT_MM and _UNIT_MM.get(width_u) else "user"
    primitives: list[VectorPrimitive] = []

    def add_poly(pts: list[tuple[float, float]], closed: bool, kind: str, layer: LayerName) -> None:
        if len(pts) < 2:
            return
        primitives.append(
            VectorPrimitive(kind=kind, layer=layer, points=pts, closed=closed, unit=unit)
        )

    for el in root.iter():
        tag = _local(el.tag)
        layer_name = el.get("data-layer") or el.get("id") or ""
        layer = _guess_layer(layer_name)
        if tag == "rect":
            x = float(el.get("x") or 0)
            y = float(el.get("y") or 0)
            w = float(el.get("width") or 0)
            h = float(el.get("height") or 0)
            add_poly([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], True, "rect", layer)
        elif tag == "circle":
            cx = float(el.get("cx") or 0)
            cy = float(el.get("cy") or 0)
            r = float(el.get("r") or 0)
            primitives.append(
                VectorPrimitive(
                    kind="circle",
                    layer=layer,
                    points=[(cx, cy)],
                    radius=r,
                    unit=unit,
                )
            )
        elif tag == "ellipse":
            cx = float(el.get("cx") or 0)
            cy = float(el.get("cy") or 0)
            rx = float(el.get("rx") or 0)
            ry = float(el.get("ry") or 0)
            pts = [
                (cx + rx * math.cos(t), cy + ry * math.sin(t))
                for t in [i * math.tau / 32 for i in range(32)]
            ]
            add_poly(pts, True, "polyline", layer)
        elif tag == "line":
            add_poly(
                [
                    (float(el.get("x1") or 0), float(el.get("y1") or 0)),
                    (float(el.get("x2") or 0), float(el.get("y2") or 0)),
                ],
                False,
                "line",
                layer,
            )
        elif tag in ("polyline", "polygon"):
            nums = _floats(el.get("points") or "")
            pts = [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
            add_poly(pts, tag == "polygon", "polyline", layer)
        elif tag == "path":
            pts, closed = _path_to_polyline(el.get("d") or "")
            add_poly(pts, closed, "polyline", layer)

    bounds = None
    if width_v is not None:
        height_v, height_u = _parse_length(root.get("height"))
        if height_v is not None:
            bounds = (0.0, 0.0, float(width_v), float(height_v))
    return VectorDocument(
        source=str(path),
        side=side,
        unit=unit,
        primitives=primitives,
        notes=f"Imported SVG ({len(primitives)} primitives). Units: {unit}.",
        design_bounds=bounds,
    )


def _guess_layer(name: str) -> LayerName:
    n = name.lower()
    if "outline" in n or "board" in n:
        return LayerName.BOARD_OUTLINE
    if "pad" in n:
        return LayerName.PADS
    if "via" in n:
        return LayerName.VIAS
    if "track" in n or "trace" in n:
        return LayerName.TRACKS
    if "hole" in n or "drill" in n:
        return LayerName.HOLES
    if "slot" in n:
        return LayerName.SLOTS
    if "silk" in n:
        return LayerName.SILKSCREEN
    if "copper" in n:
        return LayerName.COPPER
    if "comp" in n:
        return LayerName.COMPONENTS
    return LayerName.OTHER


def _path_to_polyline(d: str) -> tuple[list[tuple[float, float]], bool]:
    """Minimal path parser for M/L/H/V/Z (absolute and relative)."""
    tokens = re.findall(r"[MmLlHhVvZz]|[+-]?\d+(?:\.\d+)?", d)
    pts: list[tuple[float, float]] = []
    closed = False
    i = 0
    cx = cy = 0.0
    cmd = "M"
    while i < len(tokens):
        t = tokens[i]
        if re.match(r"[A-Za-z]", t):
            cmd = t
            i += 1
            if cmd in "Zz":
                closed = True
            continue
        try:
            val = float(t)
        except ValueError:
            i += 1
            continue
        if cmd in "MmLl":
            if i + 1 >= len(tokens):
                break
            x, y = float(tokens[i]), float(tokens[i + 1])
            if cmd == "m" or cmd == "l":
                x += cx
                y += cy
            cx, cy = x, y
            pts.append((cx, cy))
            cmd = "l" if cmd in "mm" else "L" if cmd in "Mm" else cmd
            i += 2
        elif cmd in "Hh":
            x = val if cmd == "H" else cx + val
            cx = x
            pts.append((cx, cy))
            i += 1
        elif cmd in "Vv":
            y = val if cmd == "V" else cy + val
            cy = y
            pts.append((cx, cy))
            i += 1
        else:
            # Skip unsupported commands (C/Q/A) by consuming numbers
            i += 1
    return pts, closed
