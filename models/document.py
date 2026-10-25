"""Serializable project document. Original rasters are never mutated in place."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

import numpy as np

from core.calibration import Calibration
from core.orientation import Orientation
from models.adjustments import ImageAdjustments
from models.contour import Contour
from models.dimension import Dimension
from models.enums import (
    AxisX,
    AxisY,
    OverlayMode,
    OriginMode,
    PcbSide,
    Side,
    Unit,
)
from models.landmark import Landmark
from models.mesh import LandmarkMesh
from models.point_group import PointGroup, default_pcb_groups
from models.tolerance import ToleranceProfile
from models.transform import TransformRecord
from models.vector import VectorDocument


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class BoardCoordinateSystem:
    origin_mode: OriginMode = OriginMode.TOP_LEFT
    origin_x: float = 0.0
    origin_y: float = 0.0
    axis_x: AxisX = AxisX.RIGHT
    axis_y: AxisY = AxisY.DOWN
    # Display-only zero. Changing this MUST NOT alter registration.
    display_zero_x: float | None = None
    display_zero_y: float | None = None
    display_zero_side: str = "A"

    def as_dict(self) -> dict:
        return {
            "origin_mode": self.origin_mode.value,
            "origin_x": self.origin_x,
            "origin_y": self.origin_y,
            "axis_x": self.axis_x.value,
            "axis_y": self.axis_y.value,
            "display_zero_x": self.display_zero_x,
            "display_zero_y": self.display_zero_y,
            "display_zero_side": self.display_zero_side,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "BoardCoordinateSystem":
        if not data:
            return cls()
        return cls(
            origin_mode=OriginMode(data.get("origin_mode", OriginMode.TOP_LEFT.value)),
            origin_x=float(data.get("origin_x", 0.0)),
            origin_y=float(data.get("origin_y", 0.0)),
            axis_x=AxisX(data.get("axis_x", AxisX.RIGHT.value)),
            axis_y=AxisY(data.get("axis_y", AxisY.DOWN.value)),
            display_zero_x=data.get("display_zero_x"),
            display_zero_y=data.get("display_zero_y"),
            display_zero_side=data.get("display_zero_side", "A"),
        )


@dataclass
class ImageDocument:
    side: Side
    source_path: str | None = None
    embedded_name: str | None = None
    width: int = 0
    height: int = 0
    # Original pixels. Display/orientation produce copies. Never replace with a preview.
    array: np.ndarray | None = field(default=None, repr=False, compare=False)
    original_bytes: bytes | None = field(default=None, repr=False, compare=False)
    original_format: str = ""
    original_mode: str = ""
    source_byte_count: int = 0
    adjustments: ImageAdjustments = field(default_factory=ImageAdjustments)
    orientation: Orientation = field(default_factory=Orientation)
    # DISPLAY-ONLY extra flip. Does not change work/registration coordinates.
    view_flip_x: bool = False
    view_flip_y: bool = False
    calibration: Calibration = field(default_factory=Calibration)
    pcb_side: PcbSide = PcbSide.UNKNOWN
    notes: str = ""
    is_derived: bool = False
    derivation: dict[str, Any] = field(default_factory=dict)
    vector: VectorDocument | None = None
    # Non-destructive view window in ORIGINAL source pixels. None = show full image.
    crop_x: float | None = None
    crop_y: float | None = None
    crop_w: float | None = None
    crop_h: float | None = None

    def crop_rect(self) -> tuple[float, float, float, float] | None:
        if self.crop_x is None or self.crop_y is None or self.crop_w is None or self.crop_h is None:
            return None
        if self.crop_w <= 0 or self.crop_h <= 0:
            return None
        return (float(self.crop_x), float(self.crop_y), float(self.crop_w), float(self.crop_h))

    @property
    def source_image_original(self) -> np.ndarray | None:
        """Full decoded source at original pixel dimensions."""
        return self.array

    def oriented_size(self) -> tuple[int, int]:
        return self.orientation.output_size(self.width, self.height)

    def to_oriented(self, x: float, y: float) -> tuple[float, float]:
        return self.orientation.map_point(x, y, self.width, self.height)

    def from_oriented(self, x: float, y: float) -> tuple[float, float]:
        return self.orientation.unmap_point(x, y, self.width, self.height)

    def work_to_view(self, x: float, y: float) -> tuple[float, float]:
        """Work (registration) pixel → display-only flipped pixel."""
        w, h = self.oriented_size()
        xx, yy = float(x), float(y)
        if self.view_flip_x:
            xx = (w - 1.0) - xx
        if self.view_flip_y:
            yy = (h - 1.0) - yy
        return xx, yy

    def view_to_work(self, x: float, y: float) -> tuple[float, float]:
        return self.work_to_view(x, y)  # flip is involution

    def as_dict(self) -> dict:
        return {
            "side": self.side.value,
            "source_path": self.source_path,
            "embedded_name": self.embedded_name,
            "width": self.width,
            "height": self.height,
            "orientation": self.orientation.as_dict(),
            "adjustments": self.adjustments.as_dict(),
            "original_format": self.original_format,
            "original_mode": self.original_mode,
            "source_byte_count": self.source_byte_count,
            "view_flip_x": self.view_flip_x,
            "view_flip_y": self.view_flip_y,
            "calibration": self.calibration.as_dict(),
            "pcb_side": self.pcb_side.value,
            "notes": self.notes,
            "is_derived": self.is_derived,
            "derivation": dict(self.derivation),
            "vector": None if self.vector is None else self.vector.as_dict(),
            "crop_x": self.crop_x,
            "crop_y": self.crop_y,
            "crop_w": self.crop_w,
            "crop_h": self.crop_h,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ImageDocument":
        from core.orientation import Orientation as Orient

        return cls(
            side=Side(data["side"]),
            source_path=data.get("source_path"),
            embedded_name=data.get("embedded_name"),
            width=int(data.get("width", 0)),
            height=int(data.get("height", 0)),
            orientation=Orient.from_dict(data.get("orientation")),
            adjustments=ImageAdjustments.from_dict(data.get("adjustments")),
            original_format=data.get("original_format", ""),
            original_mode=data.get("original_mode", ""),
            source_byte_count=int(data.get("source_byte_count", 0)),
            view_flip_x=bool(data.get("view_flip_x", False)),
            view_flip_y=bool(data.get("view_flip_y", False)),
            calibration=Calibration.from_dict(data.get("calibration")),
            pcb_side=PcbSide(data.get("pcb_side", PcbSide.UNKNOWN.value)),
            notes=data.get("notes", ""),
            is_derived=bool(data.get("is_derived", False)),
            derivation=dict(data.get("derivation") or {}),
            vector=VectorDocument.from_dict(data.get("vector")),
            crop_x=data.get("crop_x"),
            crop_y=data.get("crop_y"),
            crop_w=data.get("crop_w"),
            crop_h=data.get("crop_h"),
        )


@dataclass
class AuditEvent:
    timestamp: str
    action: str
    detail: str = ""

    def as_dict(self) -> dict:
        return {"timestamp": self.timestamp, "action": self.action, "detail": self.detail}

    @classmethod
    def from_dict(cls, data: dict) -> "AuditEvent":
        return cls(timestamp=data.get("timestamp", ""), action=data.get("action", ""), detail=data.get("detail", ""))


@dataclass
class ProjectData:
    name: str = "Untitled"
    created_at: str = field(default_factory=_now)
    modified_at: str = field(default_factory=_now)
    image_a: ImageDocument | None = None
    image_b: ImageDocument | None = None
    landmarks: list[Landmark] = field(default_factory=list)
    mesh: LandmarkMesh = field(default_factory=LandmarkMesh)
    groups: list[PointGroup] = field(default_factory=default_pcb_groups)
    contours: list[Contour] = field(default_factory=list)
    dimensions: list[Dimension] = field(default_factory=list)
    current_transform: TransformRecord | None = None
    transform_history: list[TransformRecord] = field(default_factory=list)
    board_cs: BoardCoordinateSystem = field(default_factory=BoardCoordinateSystem)
    tolerances: ToleranceProfile = field(default_factory=ToleranceProfile)
    display_unit: Unit = Unit.MM
    overlay_mode: OverlayMode = OverlayMode.A_ONLY
    overlay_opacity: float = 0.5  # 0 = A, 1 = B
    flicker_hz: float = 2.0
    flicker_space_shows_a: bool = True
    marker_colors: dict[str, str] = field(
        default_factory=lambda: {
            "zero": "#e74c3c",
            "scale": "#2980b9",
            "control": "#27ae60",
            "reference": "#e74c3c",
            "measurement": "#27ae60",
            "predicted": "#2ecc71",
            "residual": "#e67e22",
            "mesh_expected": "#7f8c8d",
            "mesh_actual": "#27ae60",
            "contour_a": "#3498db",
            "contour_b": "#9b59b6",
        }
    )
    display_precision: int = 4
    include_partial_in_stats: bool = False
    include_cropped_in_stats: bool = False
    include_unverified_in_stats: bool = False
    include_inferred_in_stats: bool = False
    audit: list[AuditEvent] = field(default_factory=list)
    ui_state: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    workspace: str = "PCB_COMPARISON"
    lens_distortion: str = "UNKNOWN"
    analysis_result: dict | None = None
    analysis_fingerprint: str = ""
    show_pixel_values: bool = False
    contour_line_width: float = 1.5
    contour_node_px: float = 3.0
    contour_selected_node_px: float = 6.0
    contour_handle_px: float = 4.0
    precision_drawing: bool = False
    precision_points: bool = False
    point_marker_px: float = 12.0
    center_cross_px: float = 6.0
    point_label_px: float = 11.0
    show_dimensions: bool = True
    show_contours: bool = True
    show_zero_scale: bool = True
    show_measurements: bool = True
    magnifier_on: bool = True
    magnifier_from_original: bool = True
    center_on_select: bool = False

    def next_id(self, prefix: str) -> str:
        existing = {lm.id for lm in self.landmarks} | {c.id for c in self.contours} | {d.id for d in self.dimensions}
        n = 1
        while True:
            cid = f"{prefix}_{n:03d}"
            if cid not in existing:
                return cid
            n += 1

    def as_dict(self) -> dict:
        return {
            "format": "picmp",
            "format_version": 1,
            "name": self.name,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
            "image_a": None if self.image_a is None else self.image_a.as_dict(),
            "image_b": None if self.image_b is None else self.image_b.as_dict(),
            "landmarks": [lm.as_dict() for lm in self.landmarks],
            "mesh": self.mesh.as_dict(),
            "groups": [g.as_dict() for g in self.groups],
            "contours": [c.as_dict() for c in self.contours],
            "dimensions": [d.as_dict() for d in self.dimensions],
            "current_transform": None if self.current_transform is None else self.current_transform.as_dict(),
            "transform_history": [t.as_dict() for t in self.transform_history],
            "board_cs": self.board_cs.as_dict(),
            "tolerances": self.tolerances.as_dict(),
            "display_unit": self.display_unit.value,
            "overlay_mode": self.overlay_mode.value,
            "overlay_opacity": self.overlay_opacity,
            "flicker_hz": self.flicker_hz,
            "flicker_space_shows_a": self.flicker_space_shows_a,
            "marker_colors": dict(self.marker_colors),
            "display_precision": self.display_precision,
            "include_partial_in_stats": self.include_partial_in_stats,
            "include_cropped_in_stats": self.include_cropped_in_stats,
            "include_unverified_in_stats": self.include_unverified_in_stats,
            "include_inferred_in_stats": self.include_inferred_in_stats,
            "audit": [a.as_dict() for a in self.audit],
            "ui_state": dict(self.ui_state),
            "notes": self.notes,
            "workspace": self.workspace,
            "lens_distortion": self.lens_distortion,
            "analysis_result": self.analysis_result,
            "analysis_fingerprint": self.analysis_fingerprint,
            "show_pixel_values": self.show_pixel_values,
            "contour_line_width": self.contour_line_width,
            "contour_node_px": self.contour_node_px,
            "contour_selected_node_px": self.contour_selected_node_px,
            "contour_handle_px": self.contour_handle_px,
            "precision_drawing": self.precision_drawing,
            "precision_points": self.precision_points,
            "point_marker_px": self.point_marker_px,
            "center_cross_px": self.center_cross_px,
            "point_label_px": self.point_label_px,
            "show_dimensions": self.show_dimensions,
            "show_contours": self.show_contours,
            "show_zero_scale": self.show_zero_scale,
            "show_measurements": self.show_measurements,
            "magnifier_on": self.magnifier_on,
            "magnifier_from_original": self.magnifier_from_original,
            "center_on_select": self.center_on_select,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectData":
        proj = cls(
            name=data.get("name", "Untitled"),
            created_at=data.get("created_at", _now()),
            modified_at=data.get("modified_at", _now()),
            image_a=ImageDocument.from_dict(data["image_a"]) if data.get("image_a") else None,
            image_b=ImageDocument.from_dict(data["image_b"]) if data.get("image_b") else None,
            landmarks=[Landmark.from_dict(x) for x in data.get("landmarks") or []],
            mesh=LandmarkMesh.from_dict(data.get("mesh")),
            groups=[PointGroup.from_dict(x) for x in data.get("groups") or []] or default_pcb_groups(),
            contours=[Contour.from_dict(x) for x in data.get("contours") or []],
            dimensions=[Dimension.from_dict(x) for x in data.get("dimensions") or []],
            current_transform=TransformRecord.from_dict(data["current_transform"])
            if data.get("current_transform")
            else None,
            transform_history=[TransformRecord.from_dict(x) for x in data.get("transform_history") or []],
            board_cs=BoardCoordinateSystem.from_dict(data.get("board_cs")),
            tolerances=ToleranceProfile.from_dict(data.get("tolerances")),
            display_unit=Unit(data.get("display_unit", Unit.MM.value)),
            overlay_mode=OverlayMode(data.get("overlay_mode", OverlayMode.A_ONLY.value)),
            overlay_opacity=float(data.get("overlay_opacity", 0.5)),
            flicker_hz=float(data.get("flicker_hz", 2.0)),
            flicker_space_shows_a=bool(data.get("flicker_space_shows_a", True)),
            marker_colors=dict(data.get("marker_colors") or {}),
            display_precision=int(data.get("display_precision", 4)),
            include_partial_in_stats=bool(data.get("include_partial_in_stats", False)),
            include_cropped_in_stats=bool(data.get("include_cropped_in_stats", False)),
            include_unverified_in_stats=bool(data.get("include_unverified_in_stats", False)),
            include_inferred_in_stats=bool(data.get("include_inferred_in_stats", False)),
            audit=[AuditEvent.from_dict(x) for x in data.get("audit") or []],
            ui_state=dict(data.get("ui_state") or {}),
            notes=data.get("notes", ""),
            workspace=data.get("workspace", "PCB_COMPARISON"),
            lens_distortion=data.get("lens_distortion", "UNKNOWN"),
            analysis_result=data.get("analysis_result"),
            analysis_fingerprint=data.get("analysis_fingerprint", ""),
            show_pixel_values=bool(data.get("show_pixel_values", False)),
            contour_line_width=float(data.get("contour_line_width", 1.5)),
            contour_node_px=float(data.get("contour_node_px", 3.0)),
            contour_selected_node_px=float(data.get("contour_selected_node_px", 6.0)),
            contour_handle_px=float(data.get("contour_handle_px", 4.0)),
            precision_drawing=bool(data.get("precision_drawing", False)),
            precision_points=bool(data.get("precision_points", False)),
            point_marker_px=float(data.get("point_marker_px", 12.0)),
            center_cross_px=float(data.get("center_cross_px", 6.0)),
            point_label_px=float(data.get("point_label_px", 11.0)),
            show_dimensions=bool(data.get("show_dimensions", True)),
            show_contours=bool(data.get("show_contours", True)),
            show_zero_scale=bool(data.get("show_zero_scale", True)),
            show_measurements=bool(data.get("show_measurements", True)),
            magnifier_on=bool(data.get("magnifier_on", True)),
            magnifier_from_original=bool(data.get("magnifier_from_original", True)),
            center_on_select=bool(data.get("center_on_select", False)),
        )
        defaults = {
            "zero": "#e74c3c",
            "scale": "#2980b9",
            "control": "#27ae60",
            "reference": "#e74c3c",
            "measurement": "#27ae60",
            "predicted": "#2ecc71",
            "residual": "#e67e22",
            "mesh_expected": "#7f8c8d",
            "mesh_actual": "#27ae60",
            "contour_a": "#3498db",
            "contour_b": "#9b59b6",
        }
        for k, v in defaults.items():
            proj.marker_colors.setdefault(k, v)
        return proj
