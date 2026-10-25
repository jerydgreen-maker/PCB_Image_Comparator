"""DXF import via ezdxf. Physical units preserved from $INSUNITS when present."""

from __future__ import annotations

from pathlib import Path

from models.enums import LayerName, Side
from models.vector import VectorDocument, VectorPrimitive


class DxfImportError(ValueError):
    pass


_INSUNITS_TO_MM = {
    1: 25.4,  # inches
    2: 304.8,  # feet
    4: 1.0,  # millimeters
    5: 10.0,  # centimeters
    6: 1000.0,  # meters
    10: 0.0254,  # mils? (not standard; treat as 0.001 inch if seen)
}


def _layer(name: str) -> LayerName:
    n = (name or "").lower()
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
    return LayerName.OTHER


def load_dxf(path: str | Path, side: Side = Side.B) -> VectorDocument:
    path = Path(path)
    try:
        import ezdxf
    except ImportError as exc:
        raise DxfImportError("ezdxf is not installed. Run: pip install ezdxf") from exc
    try:
        doc = ezdxf.readfile(path)
    except Exception as exc:
        raise DxfImportError(f"Could not read DXF: {exc}") from exc
    msp = doc.modelspace()
    ins = int(doc.header.get("$INSUNITS", 0) or 0)
    scale = _INSUNITS_TO_MM.get(ins, 1.0)
    unit = "mm" if ins in _INSUNITS_TO_MM else "user"
    primitives: list[VectorPrimitive] = []

    def sc(x, y):
        return (float(x) * scale, float(y) * scale)

    for e in msp:
        layer = _layer(getattr(e.dxf, "layer", ""))
        t = e.dxftype()
        if t == "LINE":
            primitives.append(
                VectorPrimitive(
                    kind="line",
                    layer=layer,
                    points=[sc(e.dxf.start.x, e.dxf.start.y), sc(e.dxf.end.x, e.dxf.end.y)],
                    unit=unit,
                )
            )
        elif t in ("LWPOLYLINE", "POLYLINE"):
            try:
                pts = [sc(p[0], p[1]) for p in e.get_points("xy")]
            except Exception:
                pts = []
                if hasattr(e, "vertices"):
                    pts = [sc(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
            closed = bool(getattr(e, "closed", False))
            primitives.append(VectorPrimitive(kind="polyline", layer=layer, points=pts, closed=closed, unit=unit))
        elif t == "CIRCLE":
            primitives.append(
                VectorPrimitive(
                    kind="circle",
                    layer=layer,
                    points=[sc(e.dxf.center.x, e.dxf.center.y)],
                    radius=float(e.dxf.radius) * scale,
                    unit=unit,
                )
            )
        elif t == "ARC":
            import math

            cx, cy = e.dxf.center.x, e.dxf.center.y
            r = float(e.dxf.radius)
            a0 = math.radians(float(e.dxf.start_angle))
            a1 = math.radians(float(e.dxf.end_angle))
            if a1 < a0:
                a1 += math.tau
            n = max(8, int((a1 - a0) / (math.pi / 16)))
            pts = [
                sc(cx + r * math.cos(a0 + (a1 - a0) * i / n), cy + r * math.sin(a0 + (a1 - a0) * i / n))
                for i in range(n + 1)
            ]
            primitives.append(VectorPrimitive(kind="polyline", layer=layer, points=pts, closed=False, unit=unit))
        elif t == "POINT":
            primitives.append(
                VectorPrimitive(kind="point", layer=layer, points=[sc(e.dxf.location.x, e.dxf.location.y)], unit=unit)
            )

    return VectorDocument(
        source=str(path),
        side=side,
        unit=unit,
        primitives=primitives,
        notes=f"Imported DXF ({len(primitives)} primitives). INSUNITS={ins}.",
    )
