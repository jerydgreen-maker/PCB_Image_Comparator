"""Reference and measurement landmarks. Types are never interchangeable."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from models.enums import Confidence, PointType, Side


@dataclass
class Landmark:
    id: str
    point_type: PointType
    label: str
    group: str = ""
    a_x: float | None = None
    a_y: float | None = None
    b_x: float | None = None
    b_y: float | None = None
    enabled: bool = True
    visible: bool = True
    confidence: Confidence = Confidence.CONFIRMED
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def has_a(self) -> bool:
        return self.a_x is not None and self.a_y is not None

    @property
    def has_b(self) -> bool:
        return self.b_x is not None and self.b_y is not None

    def coord(self, side: Side) -> tuple[float, float] | None:
        if side == Side.A:
            return (self.a_x, self.a_y) if self.has_a else None
        return (self.b_x, self.b_y) if self.has_b else None

    def set_coord(self, side: Side, x: float, y: float) -> None:
        if side == Side.A:
            self.a_x = float(x)
            self.a_y = float(y)
        else:
            self.b_x = float(x)
            self.b_y = float(y)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "point_type": self.point_type.value,
            "label": self.label,
            "group": self.group,
            "a_x": self.a_x,
            "a_y": self.a_y,
            "b_x": self.b_x,
            "b_y": self.b_y,
            "enabled": self.enabled,
            "visible": self.visible,
            "confidence": self.confidence.value,
            "notes": self.notes,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Landmark":
        return cls(
            id=data["id"],
            point_type=PointType(data["point_type"]),
            label=data.get("label", data["id"]),
            group=data.get("group", ""),
            a_x=data.get("a_x"),
            a_y=data.get("a_y"),
            b_x=data.get("b_x"),
            b_y=data.get("b_y"),
            enabled=bool(data.get("enabled", True)),
            visible=bool(data.get("visible", True)),
            confidence=Confidence(data.get("confidence", Confidence.CONFIRMED.value)),
            notes=data.get("notes", ""),
            extra=dict(data.get("extra") or {}),
        )
