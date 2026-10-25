"""Project controller. Registration and measurement stay strictly separated."""

from __future__ import annotations

import copy
import hashlib
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QUndoStack

from app.audit import record
from app.commands import MergeableAdjustCommand, MovePointCommand, RestoreCommand, SnapshotCommand
from core.diagnostics import (
    common_translation,
    evaluate_orientations,
    mirror_improvement,
    rotation_mismatch,
    scale_mismatch,
)
from core.measurements import compare_group, measurement_residual
from core.orientation import Orientation, rotate_image_array
from core.registration import RegistrationError, fit
from core.residuals import PointResidual
from core.statistics import modified_z_scores
from core.transforms import SimilarityTransform
from exporters.project_io import load_project, save_project, save_recovery
from importers.image_import import encode_png, load_image_preserving_source, qimage_to_bgr
from core.mesh import analyze_triangle, edge_stats, rebuild_automatic, triangles_from_edges
from models.contour import Contour, ContourVertex
from models.document import ImageDocument, ProjectData
from models.enums import (
    ActiveTool,
    Confidence,
    OrientationName,
    PointType,
    Side,
    TransformMode,
    TransformStatus,
    Unit,
)
from models.landmark import Landmark
from models.mesh import LandmarkMesh
from models.point_group import PointGroup
from models.transform import TransformRecord


class ProjectController(QObject):
    changed = Signal()
    imagesChanged = Signal()
    pointsChanged = Signal()
    transformChanged = Signal()
    contoursChanged = Signal()
    statusMessage = Signal(str)
    errorMessage = Signal(str)
    calibrationReady = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.data = ProjectData()
        self.undo_stack = QUndoStack(self)
        self.undo_stack.setUndoLimit(0)
        self.path: Path | None = None
        self.dirty = False
        self.active_tool = ActiveTool.PAN
        self.active_group = "CUSTOM"
        self.naming_prefix = "M"
        self.naming_start = 1
        self._pending_ref: Landmark | None = None
        self._pending_meas: Landmark | None = None
        self._pending_pair: Landmark | None = None
        self.sync_zoom = False
        self.sync_pan = False
        self.sync_cursor = False
        self.sync_selection = False
        self.magnifier_factor = 10
        self.snap_mode = None
        self.advanced_mode = False
        self.mesh_edit: str | None = None  # add | remove | lock
        self._mesh_pick: str | None = None
        self._pair_macro_before: dict | None = None
        self._pair_macro_name: str = ""
        self.undo_stack.indexChanged.connect(self._on_undo)

    # ----- helpers -----
    def image(self, side: Side) -> ImageDocument | None:
        return self.data.image_a if side == Side.A else self.data.image_b

    def mark_dirty(self) -> None:
        self.dirty = True
        self.data.modified_at = datetime.now(timezone.utc).isoformat()
        self._invalidate_analysis()
        self.changed.emit()

    def _capture(self, *keys: str) -> dict:
        return {k: copy.deepcopy(getattr(self.data, k)) for k in keys}

    def _remember(self, text: str, before: dict) -> None:
        self._suppress_undo_hook = True
        try:
            self.undo_stack.push(RestoreCommand(self.data, text, before))
        finally:
            self._suppress_undo_hook = False

    def _on_undo(self, *_args) -> None:
        if getattr(self, "_suppress_undo_hook", False):
            return
        self.pointsChanged.emit()
        self.transformChanged.emit()
        self.contoursChanged.emit()
        self.imagesChanged.emit()
        self.mark_dirty()

    def _err(self, msg: str) -> None:
        self.errorMessage.emit(msg)
        self.statusMessage.emit(msg)

    def is_locked(self) -> bool:
        rec = self.data.current_transform
        return rec is not None and rec.status == TransformStatus.LOCKED

    def accepted_transform(self) -> SimilarityTransform | None:
        rec = self.data.current_transform
        if rec is None:
            return None
        if rec.status in (TransformStatus.ACCEPTED, TransformStatus.LOCKED, TransformStatus.PROVISIONAL):
            return rec.transform
        return None

    # ----- images -----
    def load_image(self, side: Side, path: str | Path, *, derived: bool = False, derivation: dict | None = None) -> None:
        key = "image_a" if side == Side.A else "image_b"
        before = self._capture(key)
        arr, w, h, raw, meta = load_image_preserving_source(path)
        doc = ImageDocument(
            side=side,
            source_path=str(Path(path).resolve()),
            width=w,
            height=h,
            array=arr,
            original_bytes=raw,
            original_format=meta.get("format", ""),
            original_mode=meta.get("mode", ""),
            source_byte_count=len(raw),
            is_derived=derived,
            derivation=derivation or {},
        )
        if side == Side.A:
            self.data.image_a = doc
        else:
            self.data.image_b = doc
        record(self.data, "image_loaded", f"{side.value}: {path} {w}×{h} source_bytes={len(raw)}")
        self._remember(f"Load/Replace Image {side.value}", before)
        self.imagesChanged.emit()
        self.mark_dirty()
        self.statusMessage.emit(f"Loaded image {side.value}: {Path(path).name} ({w}×{h}, source {len(raw)} bytes)")

    def clear_image(self, side: Side, *, clear_side_points: bool = False) -> None:
        """Remove an image without crashing. Optionally drop that side's coordinates."""
        before = self._capture("image_a", "image_b", "landmarks", "current_transform", "mesh")
        if side == Side.A:
            self.data.image_a = None
        else:
            self.data.image_b = None
        if clear_side_points:
            kept = []
            for lm in self.data.landmarks:
                if side == Side.A:
                    lm.a_x = lm.a_y = None
                else:
                    lm.b_x = lm.b_y = None
                if lm.has_a or lm.has_b:
                    kept.append(lm)
            self.data.landmarks = kept
            self.data.current_transform = None
            self.data.mesh = LandmarkMesh()
            self._pending_ref = self._pending_meas = self._pending_pair = None
        record(self.data, "image_removed", f"{side.value} clear_points={clear_side_points}")
        self._remember("Remove image", before)
        self.imagesChanged.emit()
        self.pointsChanged.emit()
        self.transformChanged.emit()
        self.mark_dirty()
        self.statusMessage.emit(f"Removed image {side.value}")

    def load_image_from_array(
        self,
        side: Side,
        array: np.ndarray,
        *,
        label: str = "Clipboard image",
        source_path: str | None = None,
    ) -> None:
        if array is None or array.size == 0:
            self._err("No image data to load.")
            return
        arr = np.ascontiguousarray(array)
        h, w = arr.shape[:2]
        key = "image_a" if side == Side.A else "image_b"
        before = self._capture(key)
        raw = encode_png(arr)
        doc = ImageDocument(
            side=side,
            source_path=source_path,
            width=w,
            height=h,
            array=arr,
            original_bytes=raw,
            original_format="png",
            original_mode="RGB",
            source_byte_count=len(raw),
            notes=label,
        )
        if side == Side.A:
            self.data.image_a = doc
        else:
            self.data.image_b = doc
        record(self.data, "image_loaded", f"{side.value}: {label} ({w}×{h})")
        self._remember(f"Load/Replace Image {side.value}", before)
        self.imagesChanged.emit()
        self.mark_dirty()
        self.statusMessage.emit(f"Loaded {side.value}: {label} ({w}×{h})")

    def load_image_from_qimage(self, side: Side, qimage, *, label: str = "Clipboard image") -> None:
        self.load_image_from_array(side, qimage_to_bgr(qimage), label=label)

    def set_vector(self, side: Side, vector) -> None:
        doc = self.image(side)
        if doc is None:
            doc = ImageDocument(side=side, notes="Vector-only document")
            if side == Side.A:
                self.data.image_a = doc
            else:
                self.data.image_b = doc
        doc.vector = vector
        record(self.data, "vector_imported", f"{side.value}: {vector.source}")
        self.imagesChanged.emit()
        self.mark_dirty()

    def display_array(self, side: Side) -> np.ndarray | None:
        """Oriented work-space image (registration coordinates)."""
        doc = self.image(side)
        if doc is None or doc.array is None:
            return None
        return rotate_image_array(doc.array, doc.orientation)

    def view_array(self, side: Side, *, force_original: bool = False) -> np.ndarray | None:
        """Work-space image + display flip + non-destructive adjustments. Source unchanged."""
        doc = self.image(side)
        if doc is None or doc.array is None:
            return None
        adj = doc.adjustments
        want_original = bool(force_original) or adj.compare_mode == "original" or adj.is_neutral()
        if adj.enhance_mode == "OFF" and adj.is_neutral():
            want_original = True
        ident = not doc.orientation.mirror_x and not doc.orientation.mirror_y and not doc.orientation.rotate_quarter
        if ident and not doc.view_flip_x and not doc.view_flip_y and want_original:
            return doc.array
        arr = self.display_array(side)
        if arr is None:
            return None
        out = arr
        if doc.view_flip_x:
            out = np.fliplr(out)
        if doc.view_flip_y:
            out = np.flipud(out)
        if not want_original:
            from core.display import apply_adjustments

            out = apply_adjustments(out, adj)
        return np.ascontiguousarray(out)

    def original_view_array(self, side: Side) -> np.ndarray | None:
        """Oriented/flipped work image with no enhancement. Metrology source."""
        return self.view_array(side, force_original=True)

    def view_xy(self, side: Side, work_x: float, work_y: float) -> tuple[float, float]:
        doc = self.image(side)
        if doc is None:
            return work_x, work_y
        return doc.work_to_view(work_x, work_y)

    def unview_xy(self, side: Side, view_x: float, view_y: float) -> tuple[float, float]:
        doc = self.image(side)
        if doc is None:
            return view_x, view_y
        return doc.view_to_work(view_x, view_y)

    # ----- points -----
    def landmark_of_type(self, ptype: PointType) -> Landmark | None:
        return next((lm for lm in self.data.landmarks if lm.point_type == ptype), None)

    def enabled_references(self) -> list[Landmark]:
        refs = [
            lm
            for lm in self.data.landmarks
            if lm.point_type == PointType.REFERENCE and lm.enabled and lm.has_a and lm.has_b
        ]
        if refs:
            return refs
        return self.basic_correspondences()

    def basic_correspondences(self) -> list[Landmark]:
        out = []
        for ptype in (PointType.ZERO, PointType.SCALE):
            lm = self.landmark_of_type(ptype)
            if lm is not None and lm.enabled and lm.has_a and lm.has_b:
                out.append(lm)
        return out

    def measurements(self, *, confirmed_only: bool = False) -> list[Landmark]:
        out = [
            lm
            for lm in self.data.landmarks
            if lm.point_type in (PointType.MEASUREMENT, PointType.CONTROL)
        ]
        if confirmed_only:
            out = [
                lm
                for lm in out
                if lm.confidence in (Confidence.CONFIRMED, Confidence.CONFIRMED_VISIBLE)
            ]
        return out

    def correspondence_arrays(self, landmarks: list[Landmark]) -> tuple[np.ndarray, np.ndarray, list[str]]:
        src = []
        dst = []
        ids = []
        for lm in landmarks:
            if not (lm.has_a and lm.has_b):
                continue
            ax, ay = self._work_xy(Side.A, lm.a_x, lm.a_y)
            bx, by = self._work_xy(Side.B, lm.b_x, lm.b_y)
            src.append((ax, ay))
            dst.append((bx, by))
            ids.append(lm.id)
        if not src:
            return np.zeros((0, 2)), np.zeros((0, 2)), []
        return np.asarray(src, dtype=np.float64), np.asarray(dst, dtype=np.float64), ids

    def _work_xy(self, side: Side, x: float, y: float) -> tuple[float, float]:
        doc = self.image(side)
        if doc is None:
            return x, y
        return doc.to_oriented(x, y)

    def _store_from_work(self, side: Side, x: float, y: float) -> tuple[float, float]:
        doc = self.image(side)
        if doc is None:
            return x, y
        return doc.from_oriented(x, y)

    def place_point(self, side: Side, x: float, y: float) -> Landmark | None:
        """x,y are working (oriented display) coordinates."""
        ox, oy = self._store_from_work(side, x, y)
        if self.active_tool == ActiveTool.REFERENCE:
            before = self._capture("landmarks")
            result = self._place_reference(side, ox, oy)
            if result is not None:
                self._remember("Add reference", before)
            return result
        if self.active_tool == ActiveTool.MEASUREMENT:
            before = self._capture("landmarks")
            result = self._place_measurement(side, ox, oy)
            if result is not None:
                self._remember("Add measurement", before)
            return result
        if self.active_tool == ActiveTool.CALIBRATION:
            return self._place_calibration(side, ox, oy)
        if self.active_tool == ActiveTool.CONTOUR:
            self._place_contour_vertex(side, ox, oy)
            return None
        if self.active_tool == ActiveTool.ZERO:
            return self._place_pair_undoable(PointType.ZERO, side, ox, oy)
        if self.active_tool == ActiveTool.SCALE:
            return self._place_pair_undoable(PointType.SCALE, side, ox, oy)
        if self.active_tool == ActiveTool.CONTROL:
            before = self._capture("landmarks", "mesh")
            existed = self._pending_pair
            result = self._place_control(side, ox, oy)
            if result is not None:
                if result.has_a and result.has_b and existed is not None:
                    label = f"Confirm {result.label or result.id}"
                else:
                    label = f"Add {result.label or result.id}"
                self._remember(label, before)
            return result
        return None

    def _place_pair_undoable(self, ptype: PointType, side: Side, ox: float, oy: float) -> Landmark | None:
        want = f"Set {ptype.value}"
        if self._pair_macro_before is not None and self._pair_macro_name != want:
            self._remember(self._pair_macro_name, self._pair_macro_before)
            self._pair_macro_before = None
        existing = self.landmark_of_type(ptype)
        first = existing is None or not existing.coord(side) and not (existing.has_a and existing.has_b)
        if existing is None:
            self._pair_macro_before = self._capture("landmarks", "board_cs", "current_transform")
            self._pair_macro_name = f"Set {ptype.value}"
            return self._place_typed_pair(ptype, side, ox, oy)
        other = Side.B if side == Side.A else Side.A
        completing = existing.coord(other) is not None and existing.coord(side) is None
        if completing and self._pair_macro_before is not None:
            result = self._place_typed_pair(ptype, side, ox, oy)
            if result is not None:
                self._remember(self._pair_macro_name, self._pair_macro_before)
            self._pair_macro_before = None
            return result
        before = self._capture("landmarks", "board_cs", "current_transform")
        result = self._place_typed_pair(ptype, side, ox, oy)
        if result is not None:
            self._remember(f"Set {ptype.value}", before)
        return result

    def _place_reference(self, side: Side, x: float, y: float) -> Landmark:
        if self.is_locked():
            self._err("Alignment is LOCKED. Unlock before adding reference points.")
            return None  # type: ignore
        if side == Side.A:
            lm = Landmark(
                id=self.data.next_id("REF"),
                point_type=PointType.REFERENCE,
                label="",
                group="REFERENCE",
            )
            lm.label = lm.id
            lm.set_coord(Side.A, x, y)
            self.data.landmarks.append(lm)
            self._pending_ref = lm
            record(self.data, "reference_added", f"{lm.id} A=({x:.3f},{y:.3f})")
            self.pointsChanged.emit()
            self.mark_dirty()
            self.statusMessage.emit(f"{lm.id}: click corresponding point on B")
            return lm
        if self._pending_ref is None or self._pending_ref.has_b:
            # start a B-first correspondence only if an open A exists
            open_lm = next((lm for lm in self.data.landmarks if lm.point_type == PointType.REFERENCE and lm.has_a and not lm.has_b), None)
            if open_lm is None:
                self._err("Click the reference point on A first.")
                return None  # type: ignore
            self._pending_ref = open_lm
        self._pending_ref.set_coord(Side.B, x, y)
        record(self.data, "reference_added", f"{self._pending_ref.id} B=({x:.3f},{y:.3f})")
        self.pointsChanged.emit()
        self.mark_dirty()
        self.statusMessage.emit(f"{self._pending_ref.id} correspondence complete")
        self._pending_ref = None
        return self.data.landmarks[-1]

    def _place_measurement(self, side: Side, x: float, y: float) -> Landmark | None:
        rec = self.data.current_transform
        if rec is None or rec.status == TransformStatus.NONE:
            self._err("Accept a transform before placing measurement points.")
            return None
        if side == Side.A:
            prefix = self.naming_prefix or "M"
            n = self.naming_start + sum(
                1 for lm in self.data.landmarks if lm.point_type == PointType.MEASUREMENT and lm.group == self.active_group
            )
            lm = Landmark(
                id=self.data.next_id("MEAS"),
                point_type=PointType.MEASUREMENT,
                label=f"{prefix}{n}",
                group=self.active_group,
            )
            lm.set_coord(Side.A, x, y)
            self.data.landmarks.append(lm)
            self._pending_meas = lm
            record(self.data, "measurement_added", f"{lm.id} A=({x:.3f},{y:.3f})")
            self.pointsChanged.emit()
            self.mark_dirty()
            self.statusMessage.emit(f"{lm.label} predicted on B — click actual feature")
            return lm
        # B click: attach to pending measurement or nearest predicted
        target = self._pending_meas
        if target is None or target.has_b:
            target = self._nearest_open_measurement(x, y)
        if target is None:
            self._err("Click a measurement feature on A first.")
            return None
        target.set_coord(Side.B, x, y)
        self._pending_meas = None
        record(self.data, "measurement_actual", f"{target.id} B=({x:.3f},{y:.3f})")
        self.pointsChanged.emit()
        self.mark_dirty()
        return target

    def _nearest_open_measurement(self, bx: float, by: float) -> Landmark | None:
        xf = self.accepted_transform()
        if xf is None:
            return None
        best = None
        best_d = 1e18
        for lm in self.measurements():
            if not lm.has_a or lm.has_b:
                continue
            ax, ay = self._work_xy(Side.A, lm.a_x, lm.a_y)
            px, py = xf.apply_one(ax, ay)
            # predicted is in B working space; compare to stored-from-work inverse? bx,by are original
            wx, wy = self._work_xy(Side.B, bx, by)
            d = (px - wx) ** 2 + (py - wy) ** 2
            if d < best_d:
                best_d = d
                best = lm
        return best

    def _place_calibration(self, side: Side, x: float, y: float) -> None:
        doc = self.image(side)
        if doc is None:
            self._err(f"Image {side.value} is not loaded.")
            return
        extra = doc.calibration.__dict__
        pending = extra.get("_pending_cal")
        # store pending on the document notes side-channel
        pts = doc.derivation.setdefault("_cal_pts", [])
        pts.append((x, y))
        if len(pts) == 1:
            self.statusMessage.emit(f"SET SCALE {side.value}: click the other end of a known distance")
        else:
            self.statusMessage.emit(f"SET SCALE {side.value}: enter the real distance")
            self.calibrationReady.emit(side)
            self.changed.emit()

    def finish_calibration(self, side: Side, known: float, unit: Unit, unc: float | None = None) -> None:
        doc = self.image(side)
        if doc is None:
            self._err(f"Image {side.value} is not loaded.")
            return
        pts = doc.derivation.get("_cal_pts") or []
        if len(pts) < 2:
            self._err("Select two calibration points on the image first.")
            return
        snap = self._capture("image_a" if side == Side.A else "image_b")
        try:
            doc.calibration.set_from_two_points(pts[-2], pts[-1], known, unit, unc)
        except ValueError as exc:
            self._err(str(exc))
            return
        doc.derivation.pop("_cal_pts", None)
        doc.calibration.derived = False
        record(self.data, "calibration_changed", f"{side.value}: {doc.calibration.mm_per_px} mm/px")
        self._remember("Calibration change", snap)
        self.maybe_derive_calibration()
        self.imagesChanged.emit()
        self.mark_dirty()
        self.statusMessage.emit(
            f"Image {side.value} scale: {doc.calibration.mm_per_px:.6f} mm/px  "
            f"({doc.calibration.px_per_mm:.4f} px/mm)"
        )

    def clear_physical_calibration(self, side: Side | None = None) -> None:
        sides = (side,) if side is not None else (Side.A, Side.B)
        keys = []
        for s in sides:
            if self.image(s) is not None:
                keys.append("image_a" if s == Side.A else "image_b")
        if not keys:
            return
        snap = self._capture(*keys)
        for s in sides:
            doc = self.image(s)
            if doc is None:
                continue
            doc.calibration.clear()
            record(self.data, "calibration_cleared", s.value)
        self._remember("Clear physical calibration", snap)
        self.imagesChanged.emit()
        self.mark_dirty()

    def finish_scale_mm(self, side: Side, known_mm: float) -> bool:
        """Two clicks + known millimetre distance. Nothing else is asked."""
        if known_mm <= 0:
            self._err("Known distance must be positive.")
            return False
        self.finish_calibration(side, known_mm, Unit.MM, None)
        return self.image(side) is not None and self.image(side).calibration.is_calibrated

    def maybe_derive_calibration(self) -> None:
        """If one image has a trusted mm scale and A→B scale is known, derive the other."""
        rec = self.accepted_transform()
        if rec is None or rec.scale <= 1e-12:
            return
        a, b = self.image(Side.A), self.image(Side.B)
        s = rec.scale
        if a and a.calibration.is_calibrated and not a.calibration.derived:
            if b and (not b.calibration.is_calibrated or b.calibration.derived):
                b.calibration.mm_per_px = a.calibration.mm_per_px / s
                b.calibration.derived = True
                b.calibration.notes = "DERIVED FROM A + REGISTRATION"
                record(self.data, "calibration_derived", f"B from A / scale {s:.6f}")
        elif b and b.calibration.is_calibrated and not b.calibration.derived:
            if a and (not a.calibration.is_calibrated or a.calibration.derived):
                a.calibration.mm_per_px = b.calibration.mm_per_px * s
                a.calibration.derived = True
                a.calibration.notes = "DERIVED FROM B + REGISTRATION"
                record(self.data, "calibration_derived", f"A from B * scale {s:.6f}")

    def copy_calibration(self, src: Side, dst: Side) -> None:
        s = self.image(src)
        d = self.image(dst)
        if s is None or d is None or not s.calibration.is_calibrated:
            self._err("Source image is not calibrated.")
            return
        from core.calibration import copy_calibration

        d.calibration = copy_calibration(s.calibration)
        record(self.data, "calibration_copied", f"{src.value} → {dst.value} (explicit)")
        self.imagesChanged.emit()
        self.mark_dirty()

    def _place_contour_vertex(
        self,
        side: Side,
        x: float,
        y: float,
        *,
        kind: str = "line",
        c1=None,
        c2=None,
        node_type: str = "CORNER",
    ) -> None:
        open_c = next(
            (
                c
                for c in reversed(self.data.contours)
                if c.side == side and not c.closed and c.source in ("manual", "proposal")
            ),
            None,
        )
        if open_c is None:
            open_c = Contour(
                id=self.data.next_id("CTR"),
                side=side,
                label=f"Contour {side.value}",
                source="manual",
            )
            self.data.contours.append(open_c)
        snap = self._capture("contours")
        if open_c.vertices:
            open_c.vertices[-1].kind = kind
            if c1 is not None:
                open_c.vertices[-1].c2x, open_c.vertices[-1].c2y = float(c1[0]), float(c1[1])
        vert = ContourVertex(x=x, y=y, kind="line", node_type=node_type)
        if c2 is not None:
            vert.c1x, vert.c1y = float(c2[0]), float(c2[1])
            vert.node_type = "SMOOTH" if node_type == "CORNER" else node_type
        open_c.vertices.append(vert)
        record(self.data, "contour_edited", f"{open_c.id} +vertex")
        self._remember("Add contour point", snap)
        self.contoursChanged.emit()
        self.mark_dirty()

    def propose_edge(self, side: Side) -> None:
        arr = self.display_array(side)
        if arr is None:
            self._err(f"Image {side.value} is not loaded.")
            return
        from core.edge_proposal import propose_largest_edge

        try:
            contour = propose_largest_edge(arr, side, self.data.next_id("CTR"))
        except ValueError as exc:
            self._err(str(exc))
            return
        self.data.contours.append(contour)
        record(self.data, "contour_proposed", f"{contour.id} {side.value} — awaiting accept/reject")
        self.contoursChanged.emit()
        self.mark_dirty()
        self.statusMessage.emit(
            f"{contour.id} is a PROPOSAL only. Accept or reject it — it is not a physical boundary yet."
        )

    def accept_edge_proposal(self, contour_id: str, accept: bool) -> None:
        for c in self.data.contours:
            if c.id == contour_id and c.source == "proposal":
                if accept:
                    c.accepted_proposal = True
                    c.label = c.label.replace("PROPOSED ", "")
                    for v in c.vertices:
                        if v.confidence == Confidence.UNVERIFIED:
                            v.confidence = Confidence.CONFIRMED_VISIBLE
                    record(self.data, "contour_edited", f"{c.id} proposal accepted")
                else:
                    self.data.contours = [x for x in self.data.contours if x.id != contour_id]
                    record(self.data, "contour_edited", f"{contour_id} proposal rejected")
                self.contoursChanged.emit()
                self.mark_dirty()
                return
        self._err("No matching edge proposal.")

    def insert_contour_vertex(self, contour_id: str, index: int, x: float, y: float) -> None:
        snap = self._capture("contours")
        c = next((x for x in self.data.contours if x.id == contour_id), None)
        if c is None:
            self._err("Contour not found.")
            return
        idx = max(0, min(index, len(c.vertices)))
        c.vertices.insert(idx, ContourVertex(x=x, y=y))
        record(self.data, "contour_edited", f"{c.id} insert vertex {idx}")
        self._remember("Insert contour point", snap)
        self.contoursChanged.emit()
        self.mark_dirty()

    def move_contour_vertex(self, contour_id: str, index: int, x: float, y: float) -> None:
        snap = self._capture("contours")
        c = next((ct for ct in self.data.contours if ct.id == contour_id), None)
        if c is None or not (0 <= index < len(c.vertices)):
            self._err("Contour vertex not found.")
            return
        c.vertices[index].x = float(x)
        c.vertices[index].y = float(y)
        record(self.data, "contour_edited", f"{c.id} move vertex {index}")
        self._remember("Move contour point", snap)
        self.contoursChanged.emit()
        self.mark_dirty()

    def delete_contour_vertex(self, contour_id: str, index: int) -> None:
        snap = self._capture("contours")
        c = next((ct for ct in self.data.contours if ct.id == contour_id), None)
        if c is None or not (0 <= index < len(c.vertices)):
            self._err("Contour vertex not found.")
            return
        c.vertices.pop(index)
        record(self.data, "contour_edited", f"{c.id} delete vertex {index}")
        self._remember("Delete contour point", snap)
        self.contoursChanged.emit()
        self.mark_dirty()

    def close_active_contour(self, side: Side) -> None:
        for c in reversed(self.data.contours):
            if c.side == side and not c.closed:
                if len(c.vertices) < 3:
                    self._err("Contour is not closed — at least 3 points are required to close.")
                    return
                c.closed = True
                record(self.data, "contour_edited", f"{c.id} closed")
                self.contoursChanged.emit()
                self.mark_dirty()
                if self.accepted_transform() is not None and c.source != "projected":
                    if not any(x.pair_id == c.id for x in self.data.contours):
                        self.project_contour_to_other(c.id)
                return
        self._err("No open contour to close.")

    def finish_open_contour(self, side: Side) -> None:
        for c in reversed(self.data.contours):
            if c.side == side and not c.closed and c.source in ("manual", "proposal"):
                record(self.data, "contour_edited", f"{c.id} finished open")
                self.contoursChanged.emit()
                self.mark_dirty()
                return

    def cancel_last_contour_segment(self, side: Side) -> None:
        self.undo_contour_point(side)

    def project_contour_to_other(self, contour_id: str) -> None:
        """Project A contour through accepted registration. Never changes ZERO/SCALE/Mirror."""
        src = next((c for c in self.data.contours if c.id == contour_id), None)
        if src is None:
            self._err("Contour not found.")
            return
        xf = self.accepted_transform()
        if xf is None:
            self._err("Accept registration before projecting a contour.")
            return
        other = Side.B if src.side == Side.A else Side.A

        def fn(x, y):
            wx, wy = self._work_xy(src.side, x, y)
            if src.side == Side.A:
                px, py = xf.apply_one(wx, wy)
            else:
                px, py = xf.inverse().apply_one(wx, wy)
            img = self.image(other)
            if img is None:
                return px, py
            return img.from_oriented(px, py)

        from core.contour_geom import project_contour

        snap = self._capture("contours")
        dest = project_contour(
            src,
            fn,
            new_id=self.data.next_id("CTR"),
            side=other,
            label=f"{src.label} expected",
            source="projected",
        )
        dest.role = "EXPECTED"
        dest.pair_id = src.id
        self.data.contours.append(dest)
        record(self.data, "contour_projected", f"{src.id} → {dest.id} {other.value}")
        self._remember("Project contour", snap)
        self.contoursChanged.emit()
        self.mark_dirty()

    def move_contour_handle(self, contour_id: str, index: int, which: str, x: float, y: float) -> None:
        snap = self._capture("contours")
        c = next((ct for ct in self.data.contours if ct.id == contour_id), None)
        if c is None or not (0 <= index < len(c.vertices)):
            self._err("Contour handle not found.")
            return
        v = c.vertices[index]
        if which == "c1":
            v.c1x, v.c1y = float(x), float(y)
        else:
            v.c2x, v.c2y = float(x), float(y)
        from core.contour_geom import enforce_node_type

        prev = c.vertices[index - 1] if index else None
        nxt = c.vertices[index + 1] if index + 1 < len(c.vertices) else None
        enforce_node_type(prev, v, nxt)
        record(self.data, "contour_edited", f"{c.id} move handle {index}")
        self._remember("Move Bezier handle", snap)
        self.contoursChanged.emit()
        self.mark_dirty()

    def convert_contour_segment(self, contour_id: str, index: int, kind: str) -> None:
        snap = self._capture("contours")
        c = next((ct for ct in self.data.contours if ct.id == contour_id), None)
        if c is None or not (0 <= index < len(c.vertices)):
            self._err("Contour segment not found.")
            return
        c.vertices[index].kind = kind
        v = c.vertices[index]
        nxt = c.vertices[(index + 1) % len(c.vertices)] if c.vertices else None
        if kind in ("cubic", "quadratic", "arc", "bezier") and nxt is not None:
            mx, my = (v.x + nxt.x) / 2.0, (v.y + nxt.y) / 2.0
            if v.c2x is None:
                v.c2x, v.c2y = mx, my
            if nxt.c1x is None and kind == "cubic":
                nxt.c1x, nxt.c1y = mx, my
        record(self.data, "contour_edited", f"{c.id} convert {kind}")
        self._remember("Convert contour segment", snap)
        self.contoursChanged.emit()
        self.mark_dirty()

    def open_contour(self, contour_id: str) -> None:
        snap = self._capture("contours")
        c = next((ct for ct in self.data.contours if ct.id == contour_id), None)
        if c is None:
            return
        c.closed = False
        record(self.data, "contour_edited", f"{c.id} opened")
        self._remember("Open contour", snap)
        self.contoursChanged.emit()
        self.mark_dirty()

    def split_contour_segment(self, contour_id: str, index: int) -> None:
        snap = self._capture("contours")
        c = next((ct for ct in self.data.contours if ct.id == contour_id), None)
        if c is None or not c.vertices:
            return
        a = c.vertices[index % len(c.vertices)]
        b = c.vertices[(index + 1) % len(c.vertices)]
        mid = ContourVertex(x=(a.x + b.x) / 2.0, y=(a.y + b.y) / 2.0, kind=a.kind, node_type="CORNER")
        c.vertices.insert(index + 1, mid)
        record(self.data, "contour_edited", f"{c.id} split {index}")
        self._remember("Split contour segment", snap)
        self.contoursChanged.emit()
        self.mark_dirty()

    def join_contours(self, id_a: str, id_b: str) -> None:
        a = next((c for c in self.data.contours if c.id == id_a), None)
        b = next((c for c in self.data.contours if c.id == id_b), None)
        if a is None or b is None or a.side != b.side:
            self._err("Join needs two contours on the same image.")
            return
        snap = self._capture("contours")
        a.vertices.extend(b.vertices)
        a.closed = False
        self.data.contours = [c for c in self.data.contours if c.id != b.id]
        record(self.data, "contour_edited", f"join {id_a}+{id_b}")
        self._remember("Join contours", snap)
        self.contoursChanged.emit()
        self.mark_dirty()

    def reverse_contour(self, contour_id: str) -> None:
        snap = self._capture("contours")
        c = next((ct for ct in self.data.contours if ct.id == contour_id), None)
        if c is None:
            return
        from core.contour_geom import reverse_contour

        reverse_contour(c)
        record(self.data, "contour_edited", f"{c.id} reversed")
        self._remember("Reverse contour", snap)
        self.contoursChanged.emit()
        self.mark_dirty()

    def set_crop(self, side: Side, x: float, y: float, w: float, h: float) -> None:
        """Store a working region in ORIGINAL image pixels. Source raster is unchanged."""
        doc = self.image(side)
        if doc is None:
            self._err(f"Image {side.value} is not loaded.")
            return
        key = "image_a" if side == Side.A else "image_b"
        snap = self._capture(key)
        doc.crop_x, doc.crop_y, doc.crop_w, doc.crop_h = float(x), float(y), float(w), float(h)
        record(self.data, "crop_applied", f"{side.value} {w:.1f}x{h:.1f} at {x:.1f},{y:.1f}")
        self._remember("Apply crop", snap)
        self.imagesChanged.emit()
        self.mark_dirty()

    def crop_view_rect(self, side: Side) -> tuple[float, float, float, float] | None:
        """AABB of the original crop rectangle in view/scene pixels."""
        doc = self.image(side)
        if doc is None:
            return None
        cr = doc.crop_rect()
        if cr is None:
            return None
        x, y, w, h = cr
        mapped = []
        for ox, oy in ((x, y), (x + w, y), (x, y + h), (x + w, y + h)):
            wx, wy = doc.to_oriented(ox, oy)
            vx, vy = doc.work_to_view(wx, wy)
            mapped.append((vx, vy))
        xs = [p[0] for p in mapped]
        ys = [p[1] for p in mapped]
        return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    def reset_crop(self, side: Side) -> None:
        doc = self.image(side)
        if doc is None:
            return
        key = "image_a" if side == Side.A else "image_b"
        snap = self._capture(key)
        doc.crop_x = doc.crop_y = doc.crop_w = doc.crop_h = None
        record(self.data, "crop_reset", side.value)
        self._remember("Reset crop", snap)
        self.imagesChanged.emit()
        self.mark_dirty()

    def add_dimension(self, side: Side, p1, p2, offset, *, orientation: str = "ALIGNED") -> str:
        from models.dimension import Dimension

        snap = self._capture("dimensions")
        did = self.data.next_id("DIM")
        dim = Dimension(
            id=did,
            side=side,
            p1x=float(p1[0]),
            p1y=float(p1[1]),
            p2x=float(p2[0]),
            p2y=float(p2[1]),
            orientation=orientation,
            label=did,
        )
        from core.dimensions import set_offset_from_click

        set_offset_from_click(dim, float(offset[0]), float(offset[1]))
        self.data.dimensions.append(dim)
        record(self.data, "dimension_added", did)
        self._remember("Create dimension", snap)
        self.mark_dirty()
        self.changed.emit()
        return did

    def update_dimension_offset(self, dim_id: str, x: float, y: float) -> None:
        dim = next((d for d in self.data.dimensions if d.id == dim_id), None)
        if dim is None:
            return
        snap = self._capture("dimensions")
        from core.dimensions import set_offset_from_click

        set_offset_from_click(dim, x, y)
        record(self.data, "dimension_edited", f"{dim_id} offset")
        self._remember("Move dimension annotation", snap)
        self.mark_dirty()
        self.changed.emit()

    def set_dimension_orientation(self, dim_id: str, orientation: str) -> None:
        dim = next((d for d in self.data.dimensions if d.id == dim_id), None)
        if dim is None:
            return
        snap = self._capture("dimensions")
        dim.orientation = orientation
        record(self.data, "dimension_edited", f"{dim_id} {orientation}")
        self._remember("Change dimension orientation", snap)
        self.mark_dirty()
        self.changed.emit()

    def set_dimension_precision(self, dim_id: str, precision: int) -> None:
        dim = next((d for d in self.data.dimensions if d.id == dim_id), None)
        if dim is None:
            return
        snap = self._capture("dimensions")
        dim.precision = max(0, min(6, int(precision)))
        self._remember("Change dimension precision", snap)
        self.mark_dirty()
        self.changed.emit()

    def set_dimension_visible(self, dim_id: str, visible: bool) -> None:
        dim = next((d for d in self.data.dimensions if d.id == dim_id), None)
        if dim is None:
            return
        snap = self._capture("dimensions")
        dim.visible = bool(visible)
        self._remember("Hide dimension" if not visible else "Show dimension", snap)
        self.mark_dirty()
        self.changed.emit()

    def rename_dimension(self, dim_id: str, label: str, note: str = "") -> None:
        dim = next((d for d in self.data.dimensions if d.id == dim_id), None)
        if dim is None:
            return
        snap = self._capture("dimensions")
        dim.label = label
        dim.note = note
        self._remember("Rename dimension", snap)
        self.mark_dirty()
        self.changed.emit()

    def delete_dimension(self, dim_id: str) -> None:
        snap = self._capture("dimensions")
        self.data.dimensions = [d for d in self.data.dimensions if d.id != dim_id]
        record(self.data, "dimension_deleted", dim_id)
        self._remember("Delete dimension", snap)
        self.mark_dirty()
        self.changed.emit()

    def project_dimension_to_other(self, dim_id: str) -> None:
        src = next((d for d in self.data.dimensions if d.id == dim_id), None)
        if src is None:
            return
        xf = self.accepted_transform()
        if xf is None:
            self._err("Accept registration before projecting a dimension.")
            return
        other = Side.B if src.side == Side.A else Side.A

        def map_pt(x, y):
            wx, wy = self._work_xy(src.side, x, y)
            if src.side == Side.A:
                px, py = xf.apply_one(wx, wy)
            else:
                px, py = xf.inverse().apply_one(wx, wy)
            img = self.image(other)
            return img.from_oriented(px, py) if img else (px, py)

        p1 = map_pt(src.p1x, src.p1y)
        p2 = map_pt(src.p2x, src.p2y)
        off = map_pt(src.p1x + src.offset_x, src.p1y + src.offset_y)
        snap = self._capture("dimensions")
        from models.dimension import Dimension

        dest = Dimension(
            id=self.data.next_id("DIM"),
            side=other,
            p1x=p1[0],
            p1y=p1[1],
            p2x=p2[0],
            p2y=p2[1],
            orientation=src.orientation,
            precision=src.precision,
            label=f"{src.label} {other.value}",
            linked_id=src.id,
        )
        from core.dimensions import set_offset_from_click

        set_offset_from_click(dest, off[0], off[1])
        src.show_on_other = True
        src.linked_id = dest.id
        self.data.dimensions.append(dest)
        record(self.data, "dimension_projected", f"{src.id} → {dest.id}")
        self._remember("Project dimension", snap)
        self.mark_dirty()
        self.changed.emit()

    def dimension_rows(self) -> list[dict]:
        from core.dimensions import compare_lengths, measured_length_px

        rows = []
        for dim in self.data.dimensions:
            if dim.side != Side.A and not dim.linked_id:
                # still list B-only dimensions
                pass
            length = measured_length_px(dim)
            other = next((d for d in self.data.dimensions if d.id == dim.linked_id), None)
            if other is None and dim.show_on_other:
                other = next((d for d in self.data.dimensions if d.linked_id == dim.id), None)
            img = self.image(dim.side)
            scale = img.calibration.mm_per_px if img and img.calibration.is_calibrated else None
            unit = self.data.display_unit
            row = {
                "ID": dim.id,
                "TYPE": dim.orientation,
                "P1": f"{dim.p1x:.3f},{dim.p1y:.3f}",
                "P2": f"{dim.p2x:.3f},{dim.p2y:.3f}",
                "A_LENGTH": length if dim.side == Side.A else None,
                "B_LENGTH": length if dim.side == Side.B else None,
                "DELTA": None,
                "PERCENT_DELTA": None,
                "TOLERANCE": self.data.tolerances.match,
                "STATUS": "UNVERIFIED",
                "UNIT": unit.value,
            }
            if other is not None:
                la = measured_length_px(dim if dim.side == Side.A else other)
                lb = measured_length_px(dim if dim.side == Side.B else other)
                img_a, img_b = self.image(Side.A), self.image(Side.B)
                sa = img_a.calibration.mm_per_px if img_a and img_a.calibration.is_calibrated else None
                sb = img_b.calibration.mm_per_px if img_b and img_b.calibration.is_calibrated else None
                cmp = compare_lengths(la, lb, sa, sb, unit, self.data.tolerances.match, self.data.tolerances.unit)
                row.update(cmp)
            elif dim.side == Side.A:
                xf = self.accepted_transform()
                if xf is not None:
                    w1 = self._work_xy(Side.A, dim.p1x, dim.p1y)
                    w2 = self._work_xy(Side.A, dim.p2x, dim.p2y)
                    e1 = xf.apply_one(*w1)
                    e2 = xf.apply_one(*w2)
                    exp = ((e2[0] - e1[0]) ** 2 + (e2[1] - e1[1]) ** 2) ** 0.5
                    img_b = self.image(Side.B)
                    sb = img_b.calibration.mm_per_px if img_b and img_b.calibration.is_calibrated else None
                    cmp = compare_lengths(length, exp, scale, sb, unit, self.data.tolerances.match, self.data.tolerances.unit)
                    row.update(cmp)
                    row["B_LENGTH"] = cmp.get("B_LENGTH")
                    row["STATUS"] = "EXPECTED"
            rows.append(row)
        return rows

    def contour_rows(self) -> list[dict]:
        from core.contour_geom import contour_deviation, sample_contour

        rows = []
        xf = self.accepted_transform()
        for c in self.data.contours:
            exp = next((x for x in self.data.contours if x.role == "EXPECTED" and (x.pair_id == c.id or c.pair_id == x.id)), None)
            row = {
                "ID": c.id,
                "SIDE": c.side.value,
                "ROLE": c.role,
                "SEGMENTS": len(c.vertices),
                "CLOSED": c.closed,
                "MEAN": None,
                "MEDIAN": None,
                "RMSE": None,
                "MAX": None,
                "STATUS": "UNVERIFIED",
            }
            if c.role == "ACTUAL" and exp is not None:
                ev = sample_contour(exp)
                av = sample_contour(c)
                evw = np.array([self._work_xy(exp.side, float(p[0]), float(p[1])) for p in ev], dtype=np.float64)
                avw = np.array([self._work_xy(c.side, float(p[0]), float(p[1])) for p in av], dtype=np.float64)
                if xf is not None and exp.side == Side.A:
                    evw = xf.apply(evw)
                stats = contour_deviation(evw, avw)
                img = self.image(c.side)
                scale = img.calibration.mm_per_px if img and img.calibration.is_calibrated else None
                unit = self.data.display_unit if scale else Unit.PX
                def conv(v):
                    if v is None:
                        return None
                    if unit == Unit.PX or not scale:
                        return v
                    return v * scale
                row.update({
                    "MEAN": conv(stats["mean"]),
                    "MEDIAN": conv(stats["median"]),
                    "RMSE": conv(stats["rmse"]),
                    "MAX": conv(stats["max"]),
                    "UNIT": unit.value,
                })
                tol = self.data.tolerances.match
                if self.data.tolerances.unit != unit and scale:
                    if self.data.tolerances.unit == Unit.PX and unit != Unit.PX:
                        tol = tol * scale
                    elif self.data.tolerances.unit != Unit.PX and unit == Unit.PX:
                        tol = tol / scale
                mx = row.get("MAX")
                row["STATUS"] = "MATCH" if mx is not None and mx <= tol else "MISMATCH"
            rows.append(row)
        return rows

    def undo_contour_point(self, side: Side) -> None:
        for c in reversed(self.data.contours):
            if c.side == side and c.vertices:
                c.vertices.pop()
                record(self.data, "contour_edited", f"{c.id} undo vertex")
                self.contoursChanged.emit()
                self.mark_dirty()
                return

    def set_display_zero(self, side: Side, x: float, y: float) -> None:
        self.data.board_cs.display_zero_x = x
        self.data.board_cs.display_zero_y = y
        self.data.board_cs.display_zero_side = side.value
        record(self.data, "display_zero_set", f"{side.value} ({x:.3f},{y:.3f}) — registration unchanged")
        self.mark_dirty()
        self.statusMessage.emit("Display zero set. Registration was not altered.")

    def _place_typed_pair(self, ptype: PointType, side: Side, x: float, y: float) -> Landmark | None:
        if self.is_locked() and ptype in (PointType.ZERO, PointType.SCALE):
            self._err("Alignment is LOCKED. Unlock before changing ZERO or SCALE.")
            return None
        lm = self.landmark_of_type(ptype)
        if lm is None:
            lm = Landmark(
                id=ptype.value,
                point_type=ptype,
                label="ZERO 0,0" if ptype == PointType.ZERO else "SCALE",
                group="REGISTRATION",
            )
            self.data.landmarks.append(lm)
        lm.set_coord(side, x, y)
        other = Side.B if side == Side.A else Side.A
        if ptype == PointType.ZERO:
            self.data.board_cs.display_zero_x = x
            self.data.board_cs.display_zero_y = y
            self.data.board_cs.display_zero_side = side.value
        record(self.data, f"{ptype.value.lower()}_set", f"{side.value}=({x:.3f},{y:.3f})")
        self.pointsChanged.emit()
        self.mark_dirty()
        if not lm.coord(other):
            self.statusMessage.emit(
                f"{ptype.value} set on {side.value}. Now click the same physical point on MODEL {other.value}."
            )
            return lm
        if ptype == PointType.ZERO:
            self.statusMessage.emit("ZERO SET ✓  — both images share origin 0,0.")
        else:
            self.statusMessage.emit("SCALE SET ✓  — click Accept Alignment to review scale and rotation.")
        if self.landmark_of_type(PointType.ZERO) and self.landmark_of_type(PointType.SCALE):
            z = self.landmark_of_type(PointType.ZERO)
            s = self.landmark_of_type(PointType.SCALE)
            if z and z.has_a and z.has_b and s and s.has_a and s.has_b:
                self.compute_from_zero_scale()
        return lm

    def _place_control(self, side: Side, x: float, y: float) -> Landmark | None:
        rec = self.data.current_transform
        if rec is None or rec.status not in (TransformStatus.ACCEPTED, TransformStatus.LOCKED):
            self._err("Accept and lock alignment before placing CONTROL landmarks.")
            return None
        open_other = [
            lm
            for lm in self.measurements()
            if lm.enabled
            and ((side == Side.A and lm.has_b and not lm.has_a) or (side == Side.B and lm.has_a and not lm.has_b))
        ]
        target = None
        if self._pending_pair is not None and self._pending_pair in open_other:
            target = self._pending_pair
        elif open_other:
            target = self._nearest_open_control(side, x, y, open_other)
        if target is not None:
            target.set_coord(side, x, y)
            target.confidence = Confidence.CONFIRMED
            self._pending_pair = None
            record(self.data, "control_actual", f"{target.id} {side.value}=({x:.3f},{y:.3f})")
            self.pointsChanged.emit()
            self.mark_dirty()
            self._maybe_refresh_mesh()
            res = self._control_error_text(target)
            self.statusMessage.emit(f"{target.label or target.id} actual on {side.value}. {res}")
            return target
        prefix = "C"
        n = 1 + sum(1 for lm in self.data.landmarks if lm.point_type == PointType.CONTROL)
        lm = Landmark(
            id=self.data.next_id("C"),
            point_type=PointType.CONTROL,
            label=f"{prefix}{n:03d}",
            group=self.active_group or "CUSTOM",
            confidence=Confidence.UNVERIFIED,
        )
        lm.extra["origin_side"] = side.value
        lm.set_coord(side, x, y)
        self.data.landmarks.append(lm)
        self._pending_pair = lm
        record(self.data, "control_added", f"{lm.id} {side.value}=({x:.3f},{y:.3f})")
        self.pointsChanged.emit()
        self.mark_dirty()
        self._maybe_refresh_mesh()
        other = "B" if side == Side.A else "A"
        self.statusMessage.emit(f"{lm.label} on {side.value} — predicted counterpart on MODEL {other} (hollow).")
        return lm

    def _nearest_open_control(self, side: Side, x: float, y: float, candidates: list[Landmark]) -> Landmark | None:
        xf = self.accepted_transform()
        if xf is None:
            return candidates[0] if candidates else None
        wx, wy = self._work_xy(side, x, y)
        best = None
        best_d = 1e18
        for lm in candidates:
            pred = None
            if side == Side.B and lm.has_a:
                pred = self.predict_b_work(lm.a_x, lm.a_y)
            elif side == Side.A and lm.has_b:
                pred = self.predict_a_work(lm.b_x, lm.b_y)
            if pred is None:
                continue
            d = (pred[0] - wx) ** 2 + (pred[1] - wy) ** 2
            if d < best_d:
                best_d = d
                best = lm
        return best

    def _control_error_text(self, lm: Landmark) -> str:
        xf = self.accepted_transform()
        if xf is None or not (lm.has_a and lm.has_b):
            return ""
        ax, ay = self._work_xy(Side.A, lm.a_x, lm.a_y)
        bx, by = self._work_xy(Side.B, lm.b_x, lm.b_y)
        px, py = xf.apply_one(ax, ay)
        dx, dy = bx - px, by - py
        err = (dx * dx + dy * dy) ** 0.5
        ang = math.degrees(math.atan2(dy, dx)) if err else 0.0
        return f"DX={dx:.3f} DY={dy:.3f} ERROR={err:.3f} ANGLE={ang:.2f}°"

    def compute_from_zero_scale(self) -> TransformRecord | None:
        refs = self.basic_correspondences()
        if len(refs) < 2:
            self._err("ZERO and SCALE must both be set on A and B.")
            return None
        if self.is_locked():
            self._err("Alignment is LOCKED. CONTROL points never change registration.")
            return None
        src, dst, ids = self.correspondence_arrays(refs)
        try:
            result = fit(src, dst, TransformMode.SIMILARITY, point_ids=ids)
        except RegistrationError as exc:
            self._err(str(exc))
            return None
        rec = TransformRecord(
            transform=result.transform,
            status=TransformStatus.PROVISIONAL,
            mode=TransformMode.SIMILARITY,
            orientation_b=self.image(Side.B).orientation if self.image(Side.B) else Orientation(),
            residuals=[r.as_dict() for r in result.residuals],
            summary=result.summary.as_dict(),
            notes=["Basic Mode ZERO+SCALE similarity — CONTROL points were not used."],
            label="Provisional ZERO+SCALE",
        )
        self.data.current_transform = rec
        self.transformChanged.emit()
        self.mark_dirty()
        t = rec.transform
        self.statusMessage.emit(
            f"Scale A→B: {t.scale:.4f}×    Rotation: {t.rotation_deg:.3f}°    — click Accept Alignment"
        )
        return rec

    def reset_alignment(self, *, delete_controls: bool = False) -> None:
        before = self._capture("landmarks", "current_transform", "transform_history", "mesh", "board_cs")
        self.data.landmarks = [
            lm
            for lm in self.data.landmarks
            if lm.point_type not in (PointType.ZERO, PointType.SCALE)
            and not (delete_controls and lm.point_type in (PointType.CONTROL, PointType.MEASUREMENT))
        ]
        self.data.current_transform = None
        self.data.mesh = LandmarkMesh()
        self.data.board_cs.display_zero_x = None
        self.data.board_cs.display_zero_y = None
        self._pending_pair = self._pending_ref = self._pending_meas = None
        record(self.data, "alignment_reset", f"delete_controls={delete_controls}")
        self._remember("Reset alignment", before)
        self.pointsChanged.emit()
        self.transformChanged.emit()
        self.mark_dirty()
        self.statusMessage.emit("Alignment reset. ZERO, SCALE, and the accepted transform were removed.")

    def build_mesh(self, *, group: str | None = None) -> int:
        nodes = self._mesh_nodes(group)
        if len(nodes) < 2:
            self._err("Need at least 2 CONTROL landmarks to build a mesh.")
            return 0
        ids, pts = zip(*nodes)
        before = self._capture("mesh")
        rebuild_automatic(self.data.mesh, list(ids), np.asarray(pts, dtype=np.float64))
        self._remember("Build mesh", before)
        record(self.data, "mesh_built", f"{len(self.data.mesh.edges)} edges")
        self.pointsChanged.emit()
        self.mark_dirty()
        self.statusMessage.emit(f"Mesh: {len(self.data.mesh.edges)} shared edges (identical topology on A and B).")
        return len(self.data.mesh.edges)

    def clear_mesh(self) -> None:
        before = self._capture("mesh")
        self.data.mesh = LandmarkMesh(view_mode=self.data.mesh.view_mode)
        self._remember("Clear mesh", before)
        self.pointsChanged.emit()
        self.mark_dirty()

    def _mesh_nodes(self, group: str | None = None) -> list[tuple[str, np.ndarray]]:
        nodes = []
        types = {PointType.CONTROL, PointType.MEASUREMENT}
        if self.data.mesh.include_zero_scale:
            types |= {PointType.ZERO, PointType.SCALE}
        for lm in self.data.landmarks:
            if lm.point_type not in types or not lm.enabled:
                continue
            if group and lm.group != group:
                continue
            pt = None
            if lm.has_a:
                pt = np.array(self._work_xy(Side.A, lm.a_x, lm.a_y), dtype=np.float64)
            elif lm.has_b:
                pred = self.predict_a_work(lm.b_x, lm.b_y)
                if pred:
                    pt = np.array(pred, dtype=np.float64)
            if pt is not None:
                nodes.append((lm.id, pt))
        return nodes

    def mesh_points(self, side: Side, *, predicted: bool = False) -> dict[str, np.ndarray]:
        xf = self.accepted_transform()
        out: dict[str, np.ndarray] = {}
        for lm in self.data.landmarks:
            if side == Side.A:
                if predicted and lm.has_b:
                    pred = self.predict_a_work(lm.b_x, lm.b_y)
                    if pred:
                        out[lm.id] = np.array(pred, dtype=np.float64)
                elif lm.has_a:
                    out[lm.id] = np.array(self._work_xy(Side.A, lm.a_x, lm.a_y), dtype=np.float64)
            else:
                if predicted and lm.has_a and xf is not None:
                    pred = self.predict_b_work(lm.a_x, lm.a_y)
                    if pred:
                        out[lm.id] = np.array(pred, dtype=np.float64)
                elif lm.has_b:
                    out[lm.id] = np.array(self._work_xy(Side.B, lm.b_x, lm.b_y), dtype=np.float64)
        return out

    def mesh_edge_rows(self) -> list[dict]:
        """Compare EXPECTED B vs ACTUAL B. Never raw A pixels minus raw B pixels."""
        xf = self.accepted_transform()
        if xf is None:
            return [
                {
                    "POINT_A": e.a,
                    "POINT_B": e.b,
                    "LENGTH_A": None,
                    "LENGTH_B": None,
                    "DELTA": None,
                    "PERCENT_DELTA": None,
                    "NOTE": "needs registration",
                }
                for e in self.data.mesh.edges
            ]
        expected = self.mesh_points(Side.B, predicted=True)
        actual = self.mesh_points(Side.B, predicted=False)
        rows = []
        for e in self.data.mesh.edges:
            row = edge_stats(e.a, e.b, expected, actual)
            exp = row.get("LENGTH_A")
            act = row.get("LENGTH_B")
            row["EXPECTED_LENGTH"] = exp
            row["ACTUAL_LENGTH"] = act
            row["LENGTH_EXPECTED_B"] = exp
            row["LENGTH_ACTUAL_B"] = act
            img_b = self.image(Side.B)
            scale = img_b.calibration.mm_per_px if img_b and img_b.calibration.is_calibrated else None
            if scale and exp is not None:
                row["EXPECTED_LENGTH_MM"] = float(exp) * scale
                row["ACTUAL_LENGTH_MM"] = None if act is None else float(act) * scale
                row["DELTA_MM"] = None if act is None else (float(act) - float(exp)) * scale
            rows.append(row)
        return rows

    def mesh_triangle_rows(self) -> list[dict]:
        nodes = self._mesh_nodes()
        if len(nodes) < 3:
            return []
        ids, pts = zip(*nodes)
        tris = triangles_from_edges(list(ids), np.asarray(pts), self.data.mesh.edges)
        xf = self.accepted_transform()
        if xf is None:
            return []
        expected = self.mesh_points(Side.B, predicted=True)
        actual = self.mesh_points(Side.B, predicted=False)
        rows = []
        for trip in tris:
            if not all(i in expected for i in trip):
                continue
            d = analyze_triangle(trip, expected, actual).as_dict()
            d["EXPECTED_AREA"] = d.get("AREA_A")
            d["ACTUAL_AREA"] = d.get("AREA_B")
            rows.append(d)
        return rows

    def add_mesh_edge(self, id_a: str, id_b: str) -> None:
        before = self._capture("mesh")
        self.data.mesh.add_edge(id_a, id_b, manual=True)
        self._remember("Add mesh edge", before)
        self.pointsChanged.emit()
        self.mark_dirty()

    def remove_mesh_edge(self, id_a: str, id_b: str) -> None:
        before = self._capture("mesh")
        self.data.mesh.remove_edge(id_a, id_b)
        self._remember("Remove mesh edge", before)
        self.pointsChanged.emit()
        self.mark_dirty()

    def lock_mesh_edge(self, id_a: str, id_b: str) -> None:
        pair = tuple(sorted((id_a, id_b)))
        for e in self.data.mesh.edges:
            if e.pair() == pair:
                e.locked = True
                e.manual = True
                self.pointsChanged.emit()
                self.mark_dirty()
                return
        self.add_mesh_edge(id_a, id_b)
        self.lock_mesh_edge(id_a, id_b)

    def _maybe_refresh_mesh(self) -> None:
        if self.data.mesh.auto_update and self.data.mesh.edges:
            nodes = self._mesh_nodes()
            if len(nodes) >= 2:
                ids, pts = zip(*nodes)
                rebuild_automatic(self.data.mesh, list(ids), np.asarray(pts, dtype=np.float64))

    def nearest_landmark(self, side: Side, x: float, y: float, radius: float = 24.0) -> Landmark | None:
        best = None
        best_d = radius * radius
        for lm in self.data.landmarks:
            stored = lm.coord(side)
            if stored is not None:
                wx, wy = self._work_xy(side, stored[0], stored[1])
            elif side == Side.B and lm.has_a:
                pred = self.predict_b_work(lm.a_x, lm.a_y)
                if pred is None:
                    continue
                wx, wy = pred
            elif side == Side.A and lm.has_b:
                pred = self.predict_a_work(lm.b_x, lm.b_y)
                if pred is None:
                    continue
                wx, wy = pred
            else:
                continue
            d = (wx - x) ** 2 + (wy - y) ** 2
            if d < best_d:
                best_d = d
                best = lm
        return best

    def nudge_selected(self, landmark: Landmark, side: Side, dx: float, dy: float) -> None:
        old = landmark.coord(side)
        if old is None:
            return
        new = (old[0] + dx, old[1] + dy)
        cmd = MovePointCommand(self.data, landmark, side, new, old)
        self.undo_stack.push(cmd)
        self.pointsChanged.emit()
        self.mark_dirty()

    def delete_landmark(self, landmark_id: str) -> None:
        self.delete_landmarks([landmark_id])

    def delete_landmarks(self, landmark_ids: list[str]) -> None:
        wanted = {str(i) for i in landmark_ids if i}
        if not wanted:
            return
        snap = self._capture("landmarks", "mesh")
        kept = [lm for lm in self.data.landmarks if lm.id not in wanted]
        if len(kept) == len(self.data.landmarks):
            return
        self.data.landmarks = kept
        self.data.mesh.edges = [e for e in self.data.mesh.edges if e.a not in wanted and e.b not in wanted]
        record(self.data, "points_deleted", ",".join(sorted(wanted)))
        self._remember("Delete points" if len(wanted) > 1 else "Delete point", snap)
        self.pointsChanged.emit()
        self.mark_dirty()

    def reset_actual(self, landmark_id: str) -> None:
        snap = self._capture("landmarks")
        lm = next((x for x in self.data.landmarks if x.id == landmark_id), None)
        if lm is None or not lm.has_b:
            return
        lm.b_x = None
        lm.b_y = None
        lm.confidence = Confidence.UNVERIFIED
        record(self.data, "actual_reset", landmark_id)
        self._remember("Reset actual", snap)
        self.pointsChanged.emit()
        self.mark_dirty()

    def set_landmark_visible(self, landmark_id: str, visible: bool) -> None:
        self.set_landmark_attr(landmark_id, "visible", bool(visible))

    def clear_landmarks_of_types(self, types) -> None:
        types = set(types)
        ids = [lm.id for lm in self.data.landmarks if lm.point_type in types]
        self.delete_landmarks(ids)

    def delete_contour(self, contour_id: str) -> None:
        snap = self._capture("contours")
        before = len(self.data.contours)
        self.data.contours = [c for c in self.data.contours if c.id != contour_id]
        if len(self.data.contours) == before:
            return
        record(self.data, "contour_deleted", contour_id)
        self._remember("Delete contour", snap)
        self.contoursChanged.emit()
        self.mark_dirty()

    def clear_contours(self) -> None:
        snap = self._capture("contours")
        self.data.contours = []
        record(self.data, "contours_cleared", "")
        self._remember("Clear contours", snap)
        self.contoursChanged.emit()
        self.mark_dirty()

    def clear_dimensions(self) -> None:
        snap = self._capture("dimensions")
        self.data.dimensions = []
        record(self.data, "dimensions_cleared", "")
        self._remember("Clear dimensions", snap)
        self.pointsChanged.emit()
        self.mark_dirty()

    def exclude_mesh_edge(self, id_a: str, id_b: str) -> None:
        before = self._capture("mesh")
        pair = tuple(sorted((id_a, id_b)))
        for e in self.data.mesh.edges:
            if e.pair() == pair:
                if e.manual and not e.locked:
                    self.data.mesh.remove_edge(id_a, id_b)
                else:
                    e.excluded = True
                break
        self._remember("Exclude mesh edge", before)
        self.pointsChanged.emit()
        self.mark_dirty()

    def delete_selection(self, items: list[tuple]) -> None:
        """Remove mixed object types as one undoable command. Never touches images."""
        if not items:
            return
        snap = self._capture("landmarks", "mesh", "contours", "dimensions")
        wanted = {str(s[1]) for s in items if s[0] == "landmark" and s[1]}
        if wanted:
            self.data.landmarks = [lm for lm in self.data.landmarks if lm.id not in wanted]
            self.data.mesh.edges = [e for e in self.data.mesh.edges if e.a not in wanted and e.b not in wanted]
        for s in items:
            kind = s[0]
            if kind == "contour_node" and len(s) >= 3:
                c = next((ct for ct in self.data.contours if ct.id == s[1]), None)
                idx = int(s[2])
                if c is not None and 0 <= idx < len(c.vertices):
                    c.vertices.pop(idx)
                    if not c.vertices:
                        self.data.contours = [x for x in self.data.contours if x.id != c.id]
            elif kind == "contour":
                self.data.contours = [c for c in self.data.contours if c.id != s[1]]
            elif kind == "dimension":
                self.data.dimensions = [d for d in self.data.dimensions if d.id != s[1]]
            elif kind == "mesh_edge" and len(s) >= 3:
                pair = tuple(sorted((s[1], s[2])))
                kept = []
                for e in self.data.mesh.edges:
                    if e.pair() != pair:
                        kept.append(e)
                        continue
                    if e.manual and not e.locked:
                        continue
                    e.excluded = True
                    kept.append(e)
                self.data.mesh.edges = kept
        record(self.data, "selection_deleted", str(len(items)))
        self._remember("Delete selected", snap)
        self.pointsChanged.emit()
        self.contoursChanged.emit()
        self.mark_dirty()

    def set_objects_visible(self, items: list[tuple], visible: bool) -> None:
        snap = self._capture("landmarks", "dimensions", "contours")
        for s in items:
            if s[0] == "landmark":
                lm = next((x for x in self.data.landmarks if x.id == s[1]), None)
                if lm is not None:
                    lm.visible = bool(visible)
            elif s[0] == "dimension":
                dim = next((d for d in self.data.dimensions if d.id == s[1]), None)
                if dim is not None:
                    dim.visible = bool(visible)
            elif s[0] in ("contour", "contour_node"):
                c = next((x for x in self.data.contours if x.id == s[1]), None)
                if c is not None:
                    c.visible = bool(visible)
        self._remember("Show selected" if visible else "Hide selected", snap)
        self.pointsChanged.emit()
        self.contoursChanged.emit()
        self.mark_dirty()

    def show_all_hidden(self) -> None:
        snap = self._capture("landmarks", "dimensions", "contours")
        for lm in self.data.landmarks:
            lm.visible = True
        for d in self.data.dimensions:
            d.visible = True
        for c in self.data.contours:
            c.visible = True
        self._remember("Show hidden", snap)
        self.pointsChanged.emit()
        self.contoursChanged.emit()
        self.mark_dirty()

    def preview_move_landmark(self, landmark_id: str, side: Side, x: float, y: float) -> None:
        lm = next((x for x in self.data.landmarks if x.id == landmark_id), None)
        if lm is None:
            return
        lm.set_coord(side, float(x), float(y))

    def commit_move_landmark(self, landmark_id: str, side: Side, old_xy, new_xy) -> None:
        if old_xy is None or new_xy is None or tuple(old_xy) == tuple(new_xy):
            return
        lm = next((x for x in self.data.landmarks if x.id == landmark_id), None)
        if lm is None:
            return
        lm.set_coord(side, *old_xy)
        cmd = MovePointCommand(self.data, lm, side, tuple(new_xy), tuple(old_xy))
        self.undo_stack.push(cmd)
        self.pointsChanged.emit()
        self.mark_dirty()

    def clear_measurements(self) -> None:
        self.clear_landmarks_of_types((PointType.MEASUREMENT,))

    def set_landmark_attr(self, landmark_id: str, field: str, value) -> None:
        snap = self._capture("landmarks")
        for lm in self.data.landmarks:
            if lm.id != landmark_id:
                continue
            setattr(lm, field, value)
            record(self.data, f"point_{field}", f"{landmark_id}={value}")
            self._remember(f"Change {field}", snap)
            self.pointsChanged.emit()
            self.mark_dirty()
            return

    def disable_landmark(self, landmark_id: str, enabled: bool) -> None:
        self.set_landmark_attr(landmark_id, "enabled", enabled)

    def convert_measurement_to_reference(self, landmark_id: str) -> None:
        if self.is_locked():
            self._err("Unlock alignment before converting a measurement into a reference point.")
            return
        for lm in self.data.landmarks:
            if lm.id == landmark_id and lm.point_type == PointType.MEASUREMENT:
                lm.point_type = PointType.REFERENCE
                record(self.data, "measurement_converted_to_reference", landmark_id)
                self.pointsChanged.emit()
                self.mark_dirty()
                return

    # ----- registration -----
    def compute_provisional(self, mode: TransformMode) -> TransformRecord | None:
        refs = self.enabled_references()
        if mode == TransformMode.TRANSLATION and len(refs) < 1:
            self._err("At least 1 reference correspondence is required.")
            return None
        if mode in (TransformMode.RIGID, TransformMode.SIMILARITY) and len(refs) < 1:
            self._err("At least 1 reference correspondence is required.")
            return None
        if mode == TransformMode.RIGID and len(refs) < 2:
            self._err("Rigid registration requires at least 2 reference correspondences.")
            return None
        if mode == TransformMode.AFFINE and len(refs) < 3:
            self._err("Affine diagnostic requires at least 3 reference correspondences.")
            return None
        src, dst, ids = self.correspondence_arrays(refs)
        try:
            result = fit(src, dst, mode, point_ids=ids)
        except RegistrationError as exc:
            self._err(str(exc))
            return None
        rec = TransformRecord(
            transform=result.transform,
            status=TransformStatus.PROVISIONAL,
            mode=mode,
            orientation_b=self.image(Side.B).orientation if self.image(Side.B) else Orientation(),
            residuals=[r.as_dict() for r in result.residuals],
            summary=result.summary.as_dict(),
            affine=None if result.affine is None else result.affine.as_dict(),
            notes=list(result.notes),
            label=f"Provisional {mode.value}",
        )
        self.data.current_transform = rec
        self.transformChanged.emit()
        self.mark_dirty()
        return rec

    def accept_transform(self) -> None:
        rec = self.data.current_transform
        if rec is None:
            self._err("No provisional transform to accept.")
            return
        if rec.status == TransformStatus.LOCKED:
            self._err("Alignment is locked.")
            return
        snap = self._capture("current_transform", "transform_history")
        rec.status = TransformStatus.ACCEPTED
        rec.label = f"Accepted {rec.mode.value} {rec.created_at}"
        self.data.transform_history.append(copy.deepcopy(rec))
        record(self.data, "transform_accepted", f"{rec.mode.value} scale={rec.transform.scale:.6f}")
        self._remember("Accept transform", snap)
        self.maybe_derive_calibration()
        self.transformChanged.emit()
        self.mark_dirty()
        self.statusMessage.emit("Transform ACCEPTED. Lock alignment before measuring if you want it frozen.")

    def lock_alignment(self) -> None:
        rec = self.data.current_transform
        if rec is None or rec.status not in (TransformStatus.ACCEPTED, TransformStatus.LOCKED):
            self._err("Accept a transform before locking alignment.")
            return
        snap = self._capture("current_transform")
        rec.status = TransformStatus.LOCKED
        record(self.data, "alignment_locked", rec.mode.value)
        self._remember("Lock alignment", snap)
        self.transformChanged.emit()
        self.mark_dirty()
        self.statusMessage.emit("ALIGNMENT LOCKED — measurement points will not change registration.")

    def unlock_alignment(self) -> None:
        rec = self.data.current_transform
        if rec is None or rec.status != TransformStatus.LOCKED:
            return
        snap = self._capture("current_transform")
        rec.status = TransformStatus.ACCEPTED
        record(self.data, "alignment_unlocked", rec.mode.value)
        self._remember("Unlock alignment", snap)
        self.transformChanged.emit()
        self.mark_dirty()

    def apply_orientation_b(self, name: OrientationName) -> None:
        self.apply_orientation(Side.B, Orientation.from_name(name))

    def apply_orientation(self, side: Side, orient: Orientation, *, display_only: bool = False) -> None:
        doc = self.image(side)
        if doc is None:
            self._err(f"Image {side.value} is not loaded.")
            return
        key = "image_a" if side == Side.A else "image_b"
        if display_only:
            snap = self._capture(key)
            doc.view_flip_x = orient.mirror_x
            doc.view_flip_y = orient.mirror_y
            record(self.data, "display_mirror_changed", f"{side.value} view_flip=({doc.view_flip_x},{doc.view_flip_y})")
            self._remember(f"Mirror {side.value}", snap)
            self.imagesChanged.emit()
            self.mark_dirty()
            self.statusMessage.emit(
                f"DISPLAY MIRROR {side.value}: {doc.work_to_view.__doc__ and ''} "
                f"{'MIRROR X' if doc.view_flip_x else ''} {'MIRROR Y' if doc.view_flip_y else ''} "
                f"— registration unchanged."
            )
            return
        if self.is_locked():
            self._err(
                f"Alignment is LOCKED. Image orientation of {side.value} is part of registration. "
                "Unlock to change REGISTRATION orientation, or use Display Mirror (view only)."
            )
            return
        snap = self._capture(key, "current_transform")
        prev = doc.orientation
        doc.orientation = orient
        record(self.data, "orientation_changed", f"{side.value}={orient.status_label()}")
        if orient.mirror_x != prev.mirror_x and orient.mirror_y == prev.mirror_y and orient.rotate_quarter == prev.rotate_quarter:
            text = f"Mirror {side.value} Horizontally"
        elif orient.mirror_y != prev.mirror_y and orient.mirror_x == prev.mirror_x and orient.rotate_quarter == prev.rotate_quarter:
            text = f"Mirror {side.value} Vertically"
        elif orient.rotate_quarter != prev.rotate_quarter:
            text = f"Rotate {side.value} {int(orient.rotate_quarter) * 90}°"
        else:
            text = f"Orientation {side.value} {orient.status_label()}"
        self._remember(text, snap)
        z = self.landmark_of_type(PointType.ZERO)
        s = self.landmark_of_type(PointType.SCALE)
        if z and z.has_a and z.has_b and s and s.has_a and s.has_b:
            self.compute_from_zero_scale()
        self.imagesChanged.emit()
        self.mark_dirty()
        self.statusMessage.emit(f"REGISTRATION orientation {side.value}: {orient.status_label()}")

    def toggle_image_mirror(self, side: Side, *, axis: str = "x") -> None:
        doc = self.image(side)
        if doc is None:
            self._err(f"Image {side.value} is not loaded.")
            return
        if self.is_locked():
            self.toggle_display_mirror(side, axis=axis)
            return
        if axis == "x":
            self.apply_orientation(side, doc.orientation.toggled(flip_x=True))
        else:
            self.apply_orientation(side, doc.orientation.toggled(flip_y=True))

    def toggle_display_mirror(self, side: Side, *, axis: str = "x") -> None:
        doc = self.image(side)
        if doc is None:
            self._err(f"Image {side.value} is not loaded.")
            return
        key = "image_a" if side == Side.A else "image_b"
        snap = self._capture(key)
        if axis == "x":
            doc.view_flip_x = not doc.view_flip_x
        else:
            doc.view_flip_y = not doc.view_flip_y
        record(self.data, "display_mirror_changed", f"{side.value} {axis}={doc.view_flip_x if axis=='x' else doc.view_flip_y}")
        axis_name = "Horizontally" if axis == "x" else "Vertically"
        self._remember(f"Mirror {side.value} {axis_name}", snap)
        self.imagesChanged.emit()
        self.mark_dirty()
        self.statusMessage.emit(
            f"DISPLAY MIRROR {side.value} {axis.upper()}: "
            f"{'ON' if (doc.view_flip_x if axis=='x' else doc.view_flip_y) else 'OFF'} "
            f"— registration / CONTROL coordinates unchanged."
        )

    def reset_orientation(self, side: Side) -> None:
        if self.is_locked():
            doc = self.image(side)
            if doc is None:
                return
            key = "image_a" if side == Side.A else "image_b"
            snap = self._capture(key)
            doc.view_flip_x = False
            doc.view_flip_y = False
            self._remember(f"Reset display {side.value}", snap)
            self.imagesChanged.emit()
            self.mark_dirty()
            return
        self.apply_orientation(side, Orientation())

    def physical_calibration_ready(self) -> bool:
        for side in (Side.B, Side.A):
            img = self.image(side)
            if img is not None and img.calibration.is_calibrated:
                return True
        return False

    def convert_tolerance_value(self, value: float, from_unit: Unit, to_unit: Unit) -> float | None:
        if from_unit == to_unit:
            return float(value)
        scale = None
        for side in (Side.B, Side.A):
            img = self.image(side)
            if img is not None and img.calibration.is_calibrated:
                scale = img.calibration.mm_per_px
                break
        if to_unit != Unit.PX and (scale is None or scale <= 0) and from_unit == Unit.PX:
            return None
        if from_unit != Unit.PX and to_unit == Unit.PX and (scale is None or scale <= 0):
            return None

        def to_mm(v: float, u: Unit) -> float:
            if u == Unit.MM:
                return v
            if u == Unit.PX:
                return v * float(scale)
            if u == Unit.CM:
                return v * 10.0
            if u == Unit.INCH:
                return v * 25.4
            if u == Unit.MIL:
                return v * 0.0254
            return v

        def from_mm(mm: float, u: Unit) -> float:
            if u == Unit.MM:
                return mm
            if u == Unit.PX:
                return mm / float(scale)
            if u == Unit.CM:
                return mm / 10.0
            if u == Unit.INCH:
                return mm / 25.4
            if u == Unit.MIL:
                return mm / 0.0254
            return mm

        return from_mm(to_mm(float(value), from_unit), to_unit)

    def set_compare_tolerance(self, value: float, unit: Unit) -> bool:
        """Store the user tolerance. Physical units stay physical even when uncalibrated."""
        snap = self._capture("tolerances", "analysis_result", "analysis_fingerprint")
        self.data.tolerances.match = float(value)
        self.data.tolerances.warning = float(value)
        self.data.tolerances.fail = float(value)
        self.data.tolerances.unit = unit
        self._invalidate_analysis()
        record(self.data, "tolerance_changed", f"{value} {unit.value}")
        self._remember("Change tolerance", snap)
        self.mark_dirty()
        if unit != Unit.PX and not self.physical_calibration_ready():
            self.statusMessage.emit(f"Tolerance: {value:g} {unit.value} — PHYSICAL SCALE NOT SET")
        else:
            self.statusMessage.emit(f"Tolerance: {value:g} {unit.value}")
        return True

    def set_group_tolerance(self, group: str, value: float | None) -> None:
        snap = self._capture("tolerances")
        if value is None:
            self.data.tolerances.group_match.pop(group, None)
        else:
            self.data.tolerances.group_match[group] = float(value)
        self._invalidate_analysis()
        self._remember(f"Group tolerance {group}", snap)
        self.mark_dirty()

    def set_adjustment(self, side: Side, field: str, value, *, merge: bool = True) -> None:
        doc = self.image(side)
        if doc is None:
            return
        if field not in ("enhance_mode", "compare_mode", "super_res_factor") and doc.adjustments.enhance_mode == "OFF":
            doc.adjustments.enhance_mode = "MANUAL"
        old = getattr(doc.adjustments, field)
        if old == value:
            return
        if merge:
            token = (0 if side == Side.A else 50) + (abs(hash(field)) % 40)
            cmd = MergeableAdjustCommand(self.data, side, field, old, value, f"Adjust {side.value} {field}", token)
            self._suppress_undo_hook = True
            try:
                self.undo_stack.push(cmd)
            finally:
                self._suppress_undo_hook = False
        else:
            setattr(doc.adjustments, field, value)
        self.imagesChanged.emit()
        self.mark_dirty()

    def reset_adjustments(self, side: Side | None = None) -> None:
        sides = (Side.A, Side.B) if side is None else (side,)
        snap = self._capture("image_a", "image_b")
        from models.adjustments import ImageAdjustments

        for s in sides:
            doc = self.image(s)
            if doc:
                doc.adjustments = ImageAdjustments()
        self._remember("Reset image adjustments", snap)
        self.imagesChanged.emit()
        self.mark_dirty()

    def copy_adjustments(self, src: Side, dst: Side) -> None:
        s, d = self.image(src), self.image(dst)
        if s is None or d is None:
            return
        snap = self._capture("image_a" if dst == Side.A else "image_b")
        d.adjustments = copy.deepcopy(s.adjustments)
        self._remember(f"Copy adjustments {src.value}→{dst.value}", snap)
        self.imagesChanged.emit()
        self.mark_dirty()

    def analysis_fingerprint(self) -> str:
        parts = []
        for lm in self.data.landmarks:
            parts.append(
                f"{lm.id}:{lm.point_type.value}:{lm.a_x}:{lm.a_y}:{lm.b_x}:{lm.b_y}:"
                f"{lm.enabled}:{lm.confidence.value}:{lm.group}"
            )
        rec = self.data.current_transform
        if rec:
            parts.append(f"{rec.transform.as_dict()}:{rec.status.value}")
        for side in (Side.A, Side.B):
            img = self.image(side)
            if img:
                parts.append(img.orientation.status_label())
        parts.append(f"{self.data.tolerances.match}:{self.data.tolerances.unit.value}:{sorted(self.data.tolerances.group_match.items())}")
        parts.append(repr(self.data.mesh.as_dict()))
        return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()

    def _invalidate_analysis(self) -> None:
        if self.data.analysis_result:
            self.data.analysis_result["stale"] = True

    def apply_adjustment_preset(self, side: Side, name: str) -> None:
        doc = self.image(side)
        if doc is None:
            return
        snap = self._capture("image_a" if side == Side.A else "image_b")
        doc.adjustments.apply_preset(name)
        self._remember(f"Preset {name} {side.value}", snap)
        self.imagesChanged.emit()
        self.mark_dirty()

    def apply_auto_enhance(self, side: Side) -> bool:
        """Propose AUTO ENHANCE parameters from original pixels. Non-destructive."""
        doc = self.image(side)
        src = self.original_view_array(side)
        if doc is None or src is None:
            return False
        from core.auto_enhance import propose_auto_adjustments

        snap = self._capture("image_a" if side == Side.A else "image_b")
        proposed = propose_auto_adjustments(src)
        proposed.compare_mode = doc.adjustments.compare_mode
        doc.adjustments = proposed
        self._remember(f"Auto enhance {side.value}", snap)
        self.imagesChanged.emit()
        self.mark_dirty()
        return True

    def set_enhance_mode(self, side: Side, mode: str) -> None:
        doc = self.image(side)
        if doc is None:
            return
        if mode not in ("OFF", "MANUAL", "AUTO", "ADVANCED"):
            return
        if mode == "OFF":
            snap = self._capture("image_a" if side == Side.A else "image_b")
            from models.adjustments import ImageAdjustments

            cmp = doc.adjustments.compare_mode
            doc.adjustments = ImageAdjustments(compare_mode=cmp, enhance_mode="OFF")
            self._remember(f"Enhance OFF {side.value}", snap)
            self.imagesChanged.emit()
            self.mark_dirty()
            return
        self.set_adjustment(side, "enhance_mode", mode, merge=False)

    def set_enhance_compare(self, side: Side, mode: str) -> None:
        doc = self.image(side)
        if doc is None:
            return
        if mode not in ("original", "enhanced", "split", "flicker"):
            return
        self.set_adjustment(side, "compare_mode", mode, merge=False)

    def export_enhanced_png(self, side: Side, path: str | Path) -> bool:
        """Write a derived visual PNG. Never overwrites the source file."""
        arr = self.view_array(side)
        if arr is None:
            return False
        dest = Path(path)
        if dest.suffix.lower() != ".png":
            dest = dest.with_suffix(".png")
        if not dest.stem.endswith("_enhanced"):
            dest = dest.with_name(f"{dest.stem}_enhanced.png")
        from importers.image_import import encode_png

        dest.write_bytes(encode_png(arr))
        record(self.data, "export_enhanced", f"{side.value} {dest.name}")
        return True

    def image_source_info(self, side: Side) -> dict:
        doc = self.image(side)
        if doc is None:
            return {}
        cal = doc.calibration
        return {
            "side": side.value,
            "format": doc.original_format or (Path(doc.source_path).suffix.lstrip(".") if doc.source_path else "unknown"),
            "width": doc.width,
            "height": doc.height,
            "mode": doc.original_mode,
            "file_size": doc.source_byte_count or (len(doc.original_bytes) if doc.original_bytes else 0),
            "has_original_bytes": bool(doc.original_bytes),
            "source_path": doc.source_path or "",
            "calibrated": cal.is_calibrated,
            "mm_per_px": cal.mm_per_px if cal.is_calibrated else None,
        }

    def calculate_comparison(self) -> dict:
        from core.analysis import run_comparison

        try:
            result = run_comparison(self)
        except ValueError as exc:
            self._err(str(exc))
            return {}
        self.data.analysis_result = result.as_dict()
        self.data.analysis_fingerprint = result.fingerprint
        self.dirty = True
        self.data.modified_at = datetime.now(timezone.utc).isoformat()
        self.changed.emit()
        self.statusMessage.emit(
            f"CALCULATE: {result.matched}/{result.confirmed} MATCH  "
            f"point rate {_fmt_pct(result.point_match_rate)}  "
            f"geometric {_fmt_pct(result.geometric_score)}"
        )
        return result.as_dict()

    def analysis_is_stale(self) -> bool:
        res = self.data.analysis_result
        if not res:
            return False
        return bool(res.get("stale")) or res.get("fingerprint") != self.analysis_fingerprint()

    def test_orientations(self, mode: TransformMode) -> list[dict]:
        refs = self.enabled_references()
        if len(refs) < 1:
            self._err("At least 1 reference correspondence is required.")
            return []
        # Evaluate orientations against original B coordinates (ignore current orientation)
        src = []
        dst = []
        for lm in refs:
            src.append((lm.a_x, lm.a_y))
            dst.append((lm.b_x, lm.b_y))
        src_a = np.asarray(src, dtype=np.float64)
        dst_b = np.asarray(dst, dtype=np.float64)
        b = self.image(Side.B)
        meas = [lm for lm in self.measurements() if lm.has_a and lm.has_b]
        ms = md = None
        if meas:
            ms = np.array([[lm.a_x, lm.a_y] for lm in meas], dtype=np.float64)
            md = np.array([[lm.b_x, lm.b_y] for lm in meas], dtype=np.float64)
        return evaluate_orientations(src_a, dst_b, b.width if b else 1, b.height if b else 1, mode, ms, md)

    def accept_orientation(self, name: OrientationName, mode: TransformMode) -> None:
        self.apply_orientation_b(name)
        self.compute_provisional(mode)
        record(self.data, "orientation_accepted", name.value)
        self.statusMessage.emit(f"Orientation {name.value} applied. Review and ACCEPT TRANSFORM.")

    # ----- residuals / diagnostics -----
    def measurement_residuals(self, *, confirmed_only: bool = True) -> list[PointResidual]:
        xf = self.accepted_transform()
        if xf is None:
            return []
        out = []
        for lm in self.measurements(confirmed_only=confirmed_only):
            if not (lm.enabled and lm.has_a and lm.has_b):
                continue
            if not self._include_confidence(lm.confidence):
                continue
            ax, ay = self._work_xy(Side.A, lm.a_x, lm.a_y)
            bx, by = self._work_xy(Side.B, lm.b_x, lm.b_y)
            out.append(measurement_residual(xf, ax, ay, bx, by, lm.id))
        return out

    def _include_confidence(self, conf: Confidence) -> bool:
        if conf in (Confidence.CONFIRMED, Confidence.CONFIRMED_VISIBLE):
            return True
        if conf in (Confidence.PARTIAL, Confidence.PARTIALLY_VISIBLE):
            return self.data.include_partial_in_stats
        if conf == Confidence.CROPPED:
            return self.data.include_cropped_in_stats
        if conf == Confidence.UNVERIFIED:
            return self.data.include_unverified_in_stats
        if conf == Confidence.INFERRED:
            return self.data.include_inferred_in_stats
        return False

    def predict_b_work(self, ax_orig: float, ay_orig: float) -> tuple[float, float] | None:
        xf = self.accepted_transform()
        if xf is None:
            return None
        ax, ay = self._work_xy(Side.A, ax_orig, ay_orig)
        return xf.apply_one(ax, ay)

    def predict_a_work(self, bx_orig: float, by_orig: float) -> tuple[float, float] | None:
        xf = self.accepted_transform()
        if xf is None:
            return None
        bx, by = self._work_xy(Side.B, bx_orig, by_orig)
        return xf.inverse().apply_one(bx, by)

    def group_comparisons(self) -> list:
        xf = self.accepted_transform()
        out = []
        for g in self.data.groups:
            pts_a = []
            pts_b = []
            for lm in self.data.landmarks:
                if lm.group != g.name or not lm.enabled:
                    continue
                if lm.has_a:
                    pts_a.append(self._work_xy(Side.A, lm.a_x, lm.a_y))
                if lm.has_b:
                    pts_b.append(self._work_xy(Side.B, lm.b_x, lm.b_y))
            if not pts_a:
                continue
            out.append(
                compare_group(
                    g.name,
                    np.asarray(pts_a, dtype=np.float64),
                    np.asarray(pts_b, dtype=np.float64) if pts_b else None,
                    transform=xf,
                    match_tol=self._tol_in_px_b(),
                )
            )
        return out

    def _tol_in_px_b(self) -> float | None:
        img = self.image(Side.B)
        if img is None or not img.calibration.is_calibrated:
            return None
        # tolerances stored in profile unit — convert to mm then px
        from models.units import physical_to_mm

        mm = physical_to_mm(self.data.tolerances.match, self.data.tolerances.unit)
        return mm / img.calibration.mm_per_px

    def run_diagnostics(self) -> list:
        findings = []
        xf = self.accepted_transform()
        refs = self.enabled_references()
        if xf and len(refs) >= 2:
            src, dst, ids = self.correspondence_arrays(refs)
            origins = src - src.mean(axis=0)
            res = self.measurement_residuals(confirmed_only=False) or []
            # use reference residuals if no measurements
            if not res:
                pred = xf.apply(src)
                from core.residuals import residual_summary

                res = residual_summary(dst, pred, ids)
            f = scale_mismatch(res, origins[: len(res)] if len(origins) >= len(res) else np.array([[r.predicted_x, r.predicted_y] for r in res]))
            if f:
                findings.append(f)
            f = rotation_mismatch(res, np.array([[r.predicted_x, r.predicted_y] for r in res]))
            if f:
                findings.append(f)
        grouped: dict[str, list[PointResidual]] = {}
        for r in self.measurement_residuals(confirmed_only=True):
            lm = next((x for x in self.data.landmarks if x.id == r.point_id), None)
            key = lm.group if lm and lm.group else "UNGROUPED"
            grouped.setdefault(key, []).append(r)
        tol = self._tol_in_px_b() or 2.0
        f = common_translation(grouped, agree_tol=tol)
        if f:
            findings.append(f)
        if refs and self.image(Side.B):
            src, dst, _ = self.correspondence_arrays(refs)
            # map dst back to original for mirror test
            b = self.image(Side.B)
            dst_orig = []
            for lm in refs:
                dst_orig.append((lm.b_x, lm.b_y))
            src_orig = np.array([[lm.a_x, lm.a_y] for lm in refs], dtype=np.float64)
            f = mirror_improvement(
                src_orig,
                np.asarray(dst_orig, dtype=np.float64),
                b.width,
                b.height,
                self.data.current_transform.mode if self.data.current_transform else TransformMode.SIMILARITY,
            )
            if f:
                findings.append(f)
        return findings

    def outlier_ids(self) -> set[str]:
        rec = self.data.current_transform
        if rec is None or not rec.residuals:
            return set()
        dists = [r.get("distance", 0.0) for r in rec.residuals]
        z = modified_z_scores(dists)
        out = set()
        for zscore, r in zip(z, rec.residuals):
            if abs(float(zscore)) >= self.data.tolerances.outlier_z:
                out.add(r.get("point_id"))
        return out

    # ----- io -----
    def save(self, path: str | Path | None = None) -> Path:
        dest = Path(path) if path else self.path
        if dest is None:
            raise ValueError("No project path.")
        save_project(self.data, dest)
        self.path = dest
        self.dirty = False
        record(self.data, "project_saved", str(dest))
        # re-save to persist the audit entry
        save_project(self.data, dest)
        self.statusMessage.emit(f"Saved {dest}")
        return dest

    def autosave_recovery(self) -> None:
        from app.settings import recovery_path

        try:
            save_recovery(self.data, recovery_path())
        except Exception:
            pass

    def load(self, path: str | Path) -> None:
        self.data = load_project(path)
        self.path = Path(path)
        self.dirty = False
        self._pending_ref = None
        self._pending_meas = None
        self._pending_pair = None
        record(self.data, "project_loaded", str(path))
        self.imagesChanged.emit()
        self.pointsChanged.emit()
        self.transformChanged.emit()
        self.contoursChanged.emit()
        self.changed.emit()
        self.statusMessage.emit(f"Opened {path}")

    def new_project(self) -> None:
        self.data = ProjectData()
        self.path = None
        self.dirty = False
        self._pending_ref = None
        self._pending_meas = None
        self._pending_pair = None
        self.undo_stack.clear()
        self.imagesChanged.emit()
        self.pointsChanged.emit()
        self.transformChanged.emit()
        self.contoursChanged.emit()
        self.changed.emit()

    def add_group(self, name: str) -> None:
        if any(g.name == name for g in self.data.groups):
            return
        self.data.groups.append(PointGroup(name=name, naming_prefix=name[:3].upper()))
        record(self.data, "group_added", name)
        self.mark_dirty()

    def workflow_step(self) -> int:
        """1 LOAD  2 ZERO  3 SCALE  4 CONTROL  5 RESULTS"""
        if self.data.image_a is None or self.data.image_b is None:
            return 1
        zero = self.landmark_of_type(PointType.ZERO)
        if zero is None or not (zero.has_a and zero.has_b):
            return 2
        scale = self.landmark_of_type(PointType.SCALE)
        if scale is None or not (scale.has_a and scale.has_b):
            return 3
        rec = self.data.current_transform
        if rec is None or rec.status not in (TransformStatus.ACCEPTED, TransformStatus.LOCKED):
            return 3
        confirmed = [lm for lm in self.measurements() if lm.has_a and lm.has_b]
        if not confirmed:
            return 4
        return 5


def _fmt_pct(v) -> str:
    return "—" if v is None else f"{float(v):.2f}%"
