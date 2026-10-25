from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.orientation import Orientation
from core.transforms import AffineTransform, SimilarityTransform
from models.enums import OrientationName, TransformMode, TransformStatus


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TransformRecord:
    """One accepted (or provisional) registration, kept in history."""

    transform: SimilarityTransform
    status: TransformStatus = TransformStatus.PROVISIONAL
    mode: TransformMode = TransformMode.SIMILARITY
    orientation_b: Orientation = field(default_factory=Orientation)
    residuals: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    affine: dict | None = None
    notes: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    label: str = ""

    def as_dict(self) -> dict:
        return {
            "transform": self.transform.as_dict(),
            "status": self.status.value,
            "mode": self.mode.value,
            "orientation_b": self.orientation_b.as_dict(),
            "residuals": list(self.residuals),
            "summary": dict(self.summary),
            "affine": self.affine,
            "notes": list(self.notes),
            "created_at": self.created_at,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TransformRecord":
        aff = data.get("affine")
        return cls(
            transform=SimilarityTransform.from_dict(data.get("transform") or {}),
            status=TransformStatus(data.get("status", TransformStatus.PROVISIONAL.value)),
            mode=TransformMode(data.get("mode", TransformMode.SIMILARITY.value)),
            orientation_b=Orientation.from_name(
                data.get("orientation_b", {}).get("name", OrientationName.NORMAL.value)
            ),
            residuals=list(data.get("residuals") or []),
            summary=dict(data.get("summary") or {}),
            affine=aff,
            notes=list(data.get("notes") or []),
            created_at=data.get("created_at", _now()),
            label=data.get("label", ""),
        )
