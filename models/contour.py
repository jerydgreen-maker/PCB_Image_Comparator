from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from models.enums import Confidence, ContourKind, Side


@dataclass
class ContourVertex:
    x: float
    y: float
    confidence: Confidence = Confidence.CONFIRMED_VISIBLE
    kind: str = "line"  # line | arc | quadratic | cubic | bezier
    notes: str = ""
    c1x: float | None = None
    c1y: float | None = None
    c2x: float | None = None
    c2y: float | None = None
    node_type: str = "CORNER"  # CORNER | SMOOTH | SYMMETRIC

    def as_dict(self) -> dict:
        return {
            "x": self.x,
            "y": self.y,
            "confidence": self.confidence.value,
            "kind": self.kind,
            "notes": self.notes,
            "c1x": self.c1x,
            "c1y": self.c1y,
            "c2x": self.c2x,
            "c2y": self.c2y,
            "node_type": self.node_type,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ContourVertex":
        return cls(
            x=float(data["x"]),
            y=float(data["y"]),
            confidence=Confidence(data.get("confidence", Confidence.CONFIRMED_VISIBLE.value)),
            kind=data.get("kind", "line"),
            notes=data.get("notes", ""),
            c1x=data.get("c1x"),
            c1y=data.get("c1y"),
            c2x=data.get("c2x"),
            c2y=data.get("c2y"),
            node_type=data.get("node_type", "CORNER"),
        )


@dataclass
class Contour:
    id: str
    side: Side
    label: str
    vertices: list[ContourVertex] = field(default_factory=list)
    closed: bool = False
    group: str = ""
    notes: str = ""
    source: str = "manual"  # manual | proposal | svg | dxf | csv | easyeda | projected
    accepted_proposal: bool = False
    role: str = "ACTUAL"  # ACTUAL | EXPECTED
    pair_id: str = ""
    visible: bool = True

    @property
    def kind(self) -> ContourKind:
        return ContourKind.CLOSED if self.closed else ContourKind.OPEN

    def points_array(self, include: set[str] | None = None) -> np.ndarray:
        pts = []
        for v in self.vertices:
            if include is None or v.confidence.value in include:
                pts.append((v.x, v.y))
        if not pts:
            return np.zeros((0, 2), dtype=np.float64)
        return np.asarray(pts, dtype=np.float64)

    def confidence_list(self) -> list[str]:
        return [v.confidence.value for v in self.vertices]

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "side": self.side.value,
            "label": self.label,
            "vertices": [v.as_dict() for v in self.vertices],
            "closed": self.closed,
            "group": self.group,
            "notes": self.notes,
            "source": self.source,
            "accepted_proposal": self.accepted_proposal,
            "role": self.role,
            "pair_id": self.pair_id,
            "visible": self.visible,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Contour":
        return cls(
            id=data["id"],
            side=Side(data["side"]),
            label=data.get("label", data["id"]),
            vertices=[ContourVertex.from_dict(v) for v in data.get("vertices") or []],
            closed=bool(data.get("closed", False)),
            group=data.get("group", ""),
            notes=data.get("notes", ""),
            source=data.get("source", "manual"),
            accepted_proposal=bool(data.get("accepted_proposal", False)),
            role=data.get("role", "ACTUAL"),
            pair_id=data.get("pair_id", ""),
            visible=bool(data.get("visible", True)),
        )
