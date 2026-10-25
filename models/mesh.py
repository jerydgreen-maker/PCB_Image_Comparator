"""Landmark mesh topology. Shared A/B connections — never independently triangulated."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MeshEdge:
    a: str
    b: str
    locked: bool = False
    manual: bool = False
    excluded: bool = False

    def pair(self) -> tuple[str, str]:
        return tuple(sorted((self.a, self.b)))

    def as_dict(self) -> dict:
        return {"a": self.a, "b": self.b, "locked": self.locked, "manual": self.manual, "excluded": self.excluded}

    @classmethod
    def from_dict(cls, data: dict) -> "MeshEdge":
        return cls(
            a=data["a"],
            b=data["b"],
            locked=bool(data.get("locked", False)),
            manual=bool(data.get("manual", False)),
            excluded=bool(data.get("excluded", False)),
        )


@dataclass
class LandmarkMesh:
    edges: list[MeshEdge] = field(default_factory=list)
    include_zero_scale: bool = False
    view_mode: str = "GHOST"
    show_ids: bool = True
    show_points: bool = True
    show_vectors: bool = True
    show_expected: bool = True
    show_actual: bool = True
    dim_images: bool = False
    auto_update: bool = True

    def edge_set(self) -> set[tuple[str, str]]:
        return {e.pair() for e in self.edges}

    def add_edge(self, id_a: str, id_b: str, *, manual: bool = True, locked: bool = False) -> None:
        if id_a == id_b:
            return
        pair = tuple(sorted((id_a, id_b)))
        for e in self.edges:
            if e.pair() == pair:
                if manual:
                    e.manual = True
                if locked:
                    e.locked = True
                return
        self.edges.append(MeshEdge(a=pair[0], b=pair[1], manual=manual, locked=locked))

    def remove_edge(self, id_a: str, id_b: str) -> None:
        pair = tuple(sorted((id_a, id_b)))
        self.edges = [e for e in self.edges if e.pair() != pair or e.locked]

    def as_dict(self) -> dict:
        return {
            "edges": [e.as_dict() for e in self.edges],
            "include_zero_scale": self.include_zero_scale,
            "view_mode": self.view_mode,
            "show_ids": self.show_ids,
            "show_points": self.show_points,
            "show_vectors": self.show_vectors,
            "show_expected": self.show_expected,
            "show_actual": self.show_actual,
            "dim_images": self.dim_images,
            "auto_update": self.auto_update,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "LandmarkMesh":
        if not data:
            return cls()
        return cls(
            edges=[MeshEdge.from_dict(e) for e in data.get("edges") or []],
            include_zero_scale=bool(data.get("include_zero_scale", False)),
            view_mode=data.get("view_mode", "GHOST"),
            show_ids=bool(data.get("show_ids", True)),
            show_points=bool(data.get("show_points", True)),
            show_vectors=bool(data.get("show_vectors", True)),
            show_expected=bool(data.get("show_expected", True)),
            show_actual=bool(data.get("show_actual", True)),
            dim_images=bool(data.get("dim_images", False)),
            auto_update=bool(data.get("auto_update", True)),
        )
