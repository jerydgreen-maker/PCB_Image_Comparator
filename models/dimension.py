"""CAD-style linear dimensions. Endpoints stay in original image pixels."""

from __future__ import annotations

from dataclasses import dataclass, field

from models.enums import Side, Unit


class DimensionOrientation:
    ALIGNED = "ALIGNED"
    HORIZONTAL = "HORIZONTAL"
    VERTICAL = "VERTICAL"


@dataclass
class Dimension:
    id: str
    side: Side
    p1x: float
    p1y: float
    p2x: float
    p2y: float
    offset_x: float = 0.0
    offset_y: float = 0.0
    text_dx: float = 0.0
    text_dy: float = 0.0
    orientation: str = DimensionOrientation.ALIGNED
    precision: int = 2
    visible: bool = True
    label: str = ""
    note: str = ""
    show_on_other: bool = False
    linked_id: str | None = None
    unit_override: str | None = None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "side": self.side.value,
            "p1x": self.p1x,
            "p1y": self.p1y,
            "p2x": self.p2x,
            "p2y": self.p2y,
            "offset_x": self.offset_x,
            "offset_y": self.offset_y,
            "text_dx": self.text_dx,
            "text_dy": self.text_dy,
            "orientation": self.orientation,
            "precision": self.precision,
            "visible": self.visible,
            "label": self.label,
            "note": self.note,
            "show_on_other": self.show_on_other,
            "linked_id": self.linked_id,
            "unit_override": self.unit_override,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Dimension":
        return cls(
            id=data["id"],
            side=Side(data.get("side", Side.A.value)),
            p1x=float(data["p1x"]),
            p1y=float(data["p1y"]),
            p2x=float(data["p2x"]),
            p2y=float(data["p2y"]),
            offset_x=float(data.get("offset_x", 0.0)),
            offset_y=float(data.get("offset_y", 0.0)),
            text_dx=float(data.get("text_dx", 0.0)),
            text_dy=float(data.get("text_dy", 0.0)),
            orientation=data.get("orientation", DimensionOrientation.ALIGNED),
            precision=int(data.get("precision", 2)),
            visible=bool(data.get("visible", True)),
            label=data.get("label", ""),
            note=data.get("note", ""),
            show_on_other=bool(data.get("show_on_other", False)),
            linked_id=data.get("linked_id"),
            unit_override=data.get("unit_override"),
        )
