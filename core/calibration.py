"""Per-image scale calibration. Calibration is never copied silently."""

from __future__ import annotations

from dataclasses import dataclass, field

from models.enums import Unit
from models.units import physical_to_mm


@dataclass
class Calibration:
    """Independent scale for one image."""

    mm_per_px: float | None = None
    point_a: tuple[float, float] | None = None
    point_b: tuple[float, float] | None = None
    known_length: float | None = None
    known_unit: Unit = Unit.MM
    pixel_distance: float | None = None
    length_uncertainty: float | None = None  # in known_unit
    point_selection_px: float = 1.0
    notes: str = ""
    derived: bool = False

    @property
    def px_per_mm(self) -> float | None:
        if self.mm_per_px is None or self.mm_per_px == 0:
            return None
        return 1.0 / self.mm_per_px

    @property
    def is_calibrated(self) -> bool:
        import math

        return (
            self.mm_per_px is not None
            and math.isfinite(float(self.mm_per_px))
            and float(self.mm_per_px) > 0
        )

    def clear(self) -> None:
        self.mm_per_px = None
        self.point_a = None
        self.point_b = None
        self.known_length = None
        self.pixel_distance = None
        self.length_uncertainty = None
        self.notes = ""
        self.derived = False

    def looks_unusual(self) -> bool:
        """Typical PCB photos are roughly 0.002–1.0 mm/px. Unusual is allowed, but must be confirmed."""
        if not self.is_calibrated:
            return False
        return float(self.mm_per_px) < 0.002 or float(self.mm_per_px) > 1.0

    def set_from_two_points(
        self,
        p0: tuple[float, float],
        p1: tuple[float, float],
        known_length: float,
        known_unit: Unit = Unit.MM,
        length_uncertainty: float | None = None,
    ) -> None:
        import math

        if known_length <= 0:
            raise ValueError("Known length must be positive.")
        dx = p1[0] - p0[0]
        dy = p1[1] - p0[1]
        pixel_distance = math.hypot(dx, dy)
        if pixel_distance < 1e-9:
            raise ValueError("Calibration points must be distinct.")
        known_mm = physical_to_mm(known_length, known_unit)
        self.point_a = (float(p0[0]), float(p0[1]))
        self.point_b = (float(p1[0]), float(p1[1]))
        self.known_length = float(known_length)
        self.known_unit = known_unit
        self.pixel_distance = pixel_distance
        self.mm_per_px = known_mm / pixel_distance
        self.length_uncertainty = length_uncertainty

    def as_dict(self) -> dict:
        return {
            "mm_per_px": self.mm_per_px,
            "px_per_mm": self.px_per_mm,
            "point_a": self.point_a,
            "point_b": self.point_b,
            "known_length": self.known_length,
            "known_unit": self.known_unit.value,
            "pixel_distance": self.pixel_distance,
            "length_uncertainty": self.length_uncertainty,
            "point_selection_px": self.point_selection_px,
            "notes": self.notes,
            "derived": self.derived,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "Calibration":
        if not data:
            return cls()
        unit = data.get("known_unit", Unit.MM.value)
        return cls(
            mm_per_px=data.get("mm_per_px"),
            point_a=tuple(data["point_a"]) if data.get("point_a") else None,
            point_b=tuple(data["point_b"]) if data.get("point_b") else None,
            known_length=data.get("known_length"),
            known_unit=Unit(unit),
            pixel_distance=data.get("pixel_distance"),
            length_uncertainty=data.get("length_uncertainty"),
            point_selection_px=float(data.get("point_selection_px", 1.0)),
            notes=data.get("notes", ""),
            derived=bool(data.get("derived", False)),
        )


def copy_calibration(src: Calibration) -> Calibration:
    """Explicit copy — caller must request this."""
    return Calibration.from_dict(src.as_dict())
