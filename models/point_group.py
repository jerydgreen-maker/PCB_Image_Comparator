from __future__ import annotations

from dataclasses import dataclass, field

from models.enums import PCB_PRESET_GROUPS, ResultState


@dataclass
class PointGroup:
    name: str
    color: str = "#3d9a6a"
    notes: str = ""
    result: ResultState = ResultState.UNVERIFIED
    flags: list[str] = field(default_factory=list)
    naming_prefix: str = ""
    naming_start: int = 1

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "color": self.color,
            "notes": self.notes,
            "result": self.result.value,
            "flags": list(self.flags),
            "naming_prefix": self.naming_prefix,
            "naming_start": self.naming_start,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PointGroup":
        return cls(
            name=data["name"],
            color=data.get("color", "#3d9a6a"),
            notes=data.get("notes", ""),
            result=ResultState(data.get("result", ResultState.UNVERIFIED.value)),
            flags=list(data.get("flags") or []),
            naming_prefix=data.get("naming_prefix", ""),
            naming_start=int(data.get("naming_start", 1)),
        )


def default_pcb_groups() -> list[PointGroup]:
    colors = {
        "BOARD_OUTLINE": "#d4a017",
        "KEYPAD": "#3d9a6a",
        "LCD": "#2f80ed",
        "BATTERY": "#c0392b",
        "SIM": "#8e44ad",
        "CHARGER": "#16a085",
        "BOTTOM_CONNECTOR": "#e67e22",
        "EARPIECE": "#1abc9c",
        "MICROPHONE": "#9b59b6",
        "MOUNTING": "#7f8c8d",
        "OUTLINE": "#d4a017",
        "CUSTOM": "#95a5a6",
    }
    return [PointGroup(name=n, color=colors.get(n, "#3d9a6a"), naming_prefix=n[:3]) for n in PCB_PRESET_GROUPS]
