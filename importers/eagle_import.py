"""Eagle / Fusion Electronics .brd XML (Eagle 6+). Binary Eagle is not faked."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from models.enums import LayerName, Side
from models.vector import VectorDocument, VectorPrimitive


class EagleImportError(ValueError):
    pass


_LAYER_MAP = {
    "1": LayerName.COPPER,
    "16": LayerName.COPPER,
    "17": LayerName.PADS,
    "18": LayerName.VIAS,
    "20": LayerName.BOARD_OUTLINE,
    "21": LayerName.SILKSCREEN,
    "22": LayerName.SILKSCREEN,
    "44": LayerName.HOLES,
    "45": LayerName.HOLES,
    "46": LayerName.HOLES,
}


def load_eagle_brd(path: str | Path, side: Side = Side.B) -> VectorDocument:
    path = Path(path)
    raw = path.read_bytes()
    if raw[:5] != b"<?xml" and b"<eagle" not in raw[:400]:
        raise EagleImportError(
            "This .brd is not Eagle 6+ XML. Binary Eagle boards are not parsed. Export Gerber or DXF."
        )
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise EagleImportError(f"Eagle BRD XML is not well-formed: {exc}") from exc
    primitives: list[VectorPrimitive] = []
    for wire in root.iter("wire"):
        try:
            pts = [(float(wire.attrib["x1"]), float(wire.attrib["y1"])), (float(wire.attrib["x2"]), float(wire.attrib["y2"]))]
        except (KeyError, ValueError):
            continue
        layer = _LAYER_MAP.get(wire.attrib.get("layer", ""), LayerName.OTHER)
        primitives.append(VectorPrimitive(kind="line", layer=layer, points=pts, unit="mm"))
    for hole in root.iter("hole"):
        try:
            primitives.append(
                VectorPrimitive(
                    kind="hole",
                    layer=LayerName.HOLES,
                    points=[(float(hole.attrib["x"]), float(hole.attrib["y"]))],
                    radius=float(hole.attrib.get("drill", 0)) / 2.0 or None,
                    unit="mm",
                )
            )
        except (KeyError, ValueError):
            continue
    for via in root.iter("via"):
        try:
            primitives.append(
                VectorPrimitive(
                    kind="via",
                    layer=LayerName.VIAS,
                    points=[(float(via.attrib["x"]), float(via.attrib["y"]))],
                    radius=float(via.attrib.get("drill", 0)) / 2.0 or None,
                    unit="mm",
                )
            )
        except (KeyError, ValueError):
            continue
    for pad in root.iter("pad"):
        try:
            primitives.append(
                VectorPrimitive(
                    kind="pad",
                    layer=LayerName.PADS,
                    points=[(float(pad.attrib["x"]), float(pad.attrib["y"]))],
                    unit="mm",
                )
            )
        except (KeyError, ValueError):
            continue
    if not primitives:
        raise EagleImportError("Eagle XML parsed, but no wires / pads / vias / holes were found.")
    return VectorDocument(source=str(path), side=side, primitives=primitives, notes=f"Eagle BRD XML: {len(primitives)} primitives", unit="mm")
