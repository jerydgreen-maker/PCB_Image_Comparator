from __future__ import annotations

from dataclasses import dataclass, field

from models.enums import ResultState, Unit

EXAMPLE_PRESETS = {
    "Example: fine contacts": {"unit": "mm", "match": 0.20, "label": "example — not an authoritative spec"},
    "Example: board outline": {"unit": "mm", "match": 0.50, "label": "example — not an authoritative spec"},
    "Example: pixels": {"unit": "px", "match": 3.0, "label": "example — not an authoritative spec"},
}


@dataclass
class ToleranceProfile:
    name: str = "user"
    unit: Unit = Unit.MM
    match: float = 0.20
    warning: float = 0.20
    fail: float = 0.20
    contour_thresholds: list[float] = field(default_factory=lambda: [0.1, 0.25, 0.5, 1.0])
    outlier_z: float = 3.5
    group_match: dict[str, float] = field(default_factory=dict)
    pitch: float | None = None
    length: float | None = None
    angle_deg: float | None = None
    scale_pct: float | None = None
    x_tol: float | None = None
    y_tol: float | None = None
    show_circles: bool = True

    def value_for_group(self, group: str | None) -> float:
        if group and group in self.group_match:
            return float(self.group_match[group])
        return float(self.match)

    def classify(self, error: float, *, group: str | None = None) -> ResultState:
        limit = self.value_for_group(group)
        if error <= limit:
            return ResultState.MATCH
        if error <= self.warning:
            return ResultState.WARNING
        return ResultState.MISMATCH

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "unit": self.unit.value,
            "match": self.match,
            "warning": self.warning,
            "fail": self.fail,
            "contour_thresholds": list(self.contour_thresholds),
            "outlier_z": self.outlier_z,
            "group_match": dict(self.group_match),
            "pitch": self.pitch,
            "length": self.length,
            "angle_deg": self.angle_deg,
            "scale_pct": self.scale_pct,
            "x_tol": self.x_tol,
            "y_tol": self.y_tol,
            "show_circles": self.show_circles,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "ToleranceProfile":
        if not data:
            return cls()
        return cls(
            name=data.get("name", "user"),
            unit=Unit(data.get("unit", Unit.MM.value)),
            match=float(data.get("match", 0.20)),
            warning=float(data.get("warning", data.get("match", 0.20))),
            fail=float(data.get("fail", data.get("match", 0.20))),
            contour_thresholds=list(data.get("contour_thresholds") or [0.1, 0.25, 0.5, 1.0]),
            outlier_z=float(data.get("outlier_z", 3.5)),
            group_match={str(k): float(v) for k, v in (data.get("group_match") or {}).items()},
            pitch=data.get("pitch"),
            length=data.get("length"),
            angle_deg=data.get("angle_deg"),
            scale_pct=data.get("scale_pct"),
            x_tol=data.get("x_tol"),
            y_tol=data.get("y_tol"),
            show_circles=bool(data.get("show_circles", True)),
        )
