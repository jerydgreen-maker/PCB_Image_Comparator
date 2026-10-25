"""Vector PCB geometry. Layer visibility never mutates coordinates."""

from __future__ import annotations

from dataclasses import dataclass, field

from models.enums import LayerName, Side


@dataclass
class VectorPrimitive:
    kind: str  # line, polyline, circle, arc, rect, pad, via, hole, slot, text
    layer: LayerName
    points: list[tuple[float, float]] = field(default_factory=list)
    closed: bool = False
    radius: float | None = None
    width: float | None = None
    attributes: dict = field(default_factory=dict)
    unit: str = "mm"

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "layer": self.layer.value,
            "points": [list(p) for p in self.points],
            "closed": self.closed,
            "radius": self.radius,
            "width": self.width,
            "attributes": dict(self.attributes),
            "unit": self.unit,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "VectorPrimitive":
        return cls(
            kind=data["kind"],
            layer=LayerName(data.get("layer", LayerName.OTHER.value)),
            points=[(float(p[0]), float(p[1])) for p in data.get("points") or []],
            closed=bool(data.get("closed", False)),
            radius=data.get("radius"),
            width=data.get("width"),
            attributes=dict(data.get("attributes") or {}),
            unit=data.get("unit", "mm"),
        )


@dataclass
class VectorDocument:
    source: str
    side: Side
    unit: str = "mm"
    primitives: list[VectorPrimitive] = field(default_factory=list)
    layer_visible: dict[str, bool] = field(default_factory=dict)
    notes: str = ""
    # Physical bounds from file metadata — NEVER a component bbox used as outline.
    design_bounds: tuple[float, float, float, float] | None = None

    def __post_init__(self) -> None:
        if not self.layer_visible:
            self.layer_visible = {layer.value: True for layer in LayerName}

    def visible_primitives(self) -> list[VectorPrimitive]:
        return [p for p in self.primitives if self.layer_visible.get(p.layer.value, True)]

    def by_layer(self, layer: LayerName) -> list[VectorPrimitive]:
        return [p for p in self.primitives if p.layer == layer]

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "side": self.side.value,
            "unit": self.unit,
            "primitives": [p.as_dict() for p in self.primitives],
            "layer_visible": dict(self.layer_visible),
            "notes": self.notes,
            "design_bounds": list(self.design_bounds) if self.design_bounds else None,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "VectorDocument | None":
        if not data:
            return None
        bounds = data.get("design_bounds")
        return cls(
            source=data.get("source", ""),
            side=Side(data.get("side", Side.B.value)),
            unit=data.get("unit", "mm"),
            primitives=[VectorPrimitive.from_dict(p) for p in data.get("primitives") or []],
            layer_visible=dict(data.get("layer_visible") or {}),
            notes=data.get("notes", ""),
            design_bounds=tuple(bounds) if bounds else None,
        )
