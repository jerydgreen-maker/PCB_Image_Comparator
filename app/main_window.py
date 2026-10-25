"""Main window: two synchronized viewers and explicit registration controls."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QImage, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from app.project import ProjectController
from app.settings import qt_settings, recovery_path
from app.undo_routing import ProjectUndoFilter, is_live_text_editor
from core.contours import automatic_cross_sections, contour_point_residuals, contour_residual_report, register_contours, width_cross_section
from core.measurements import fit_circle, pitch_series
from core.orientation import reflection_required_for_opposite_sides
from core.snap import propose_snap
from exporters.csv_export import (
    export_calibration,
    export_contours,
    export_points,
    export_transform,
    export_width_sections,
)
from exporters.image_export import difference_image, edge_difference, export_annotated, overlay_images, save_rgb
from importers.csv_import import load_contour_csv, load_points_csv
from importers.dxf_import import load_dxf
from importers.easyeda_import import load_easyeda
from importers.image_import import IMAGE_DIALOG_FILTER, array_to_rgb, RASTER_EXTENSIONS
from importers.svg_import import load_svg
from models.enums import (
    ActiveTool,
    CrossSectionAxis,
    MeshViewMode,
    OrientationName,
    OverlayMode,
    overlay_base_side,
    overlay_is_registered,
    PcbSide,
    PointType,
    Side,
    SnapMode,
    TransformMode,
    TransformStatus,
    Unit,
)
from models.tolerance import ToleranceProfile
from ui.calibration_dialog import CalibrationDialog
from ui.contour_panel import ContourPanel
from ui.group_panel import GroupPanel
from ui.image_view import ImageView
from ui.layer_panel import LayerPanel
from ui.measurement_panel import MeasurementPanel
from ui.orientation_dialog import OrientationDialog
from ui.overlay_bar import OverlayBar
from ui.overlay_controls import OverlayControls
from ui.scale_dialog import KnownDistanceDialog
from ui.perspective_dialog import PerspectiveDialog
from ui.point_table import PointTable
from ui.report_dialog import ReportDialog
from ui.session_summary import SessionSummary
from ui.toolbar import MainToolBar
from ui.transform_dialog import TransformDialog
from ui.transform_inspector import TransformInspector
from ui.width_profile import WidthProfileWidget
from ui.viewer_pane import ViewerPane
from ui.workflow_bar import WorkflowBar

LAYOUT_VERSION = 5


def _offscreen() -> bool:
    import os

    return os.environ.get("QT_QPA_PLATFORM") == "offscreen"


def _seg_dist(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    if length2 < 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


class ToolModeManager:
    """Authoritative tool/view snapshot. Widgets observe; they do not store a second mode."""

    def __init__(self, window: "MainWindow") -> None:
        self._w = window

    def snapshot(self) -> dict:
        c = self._w.controller
        d = c.data
        mesh_on = d.mesh.view_mode != MeshViewMode.HIDDEN.value and bool(d.mesh.edges)
        enhance_a = None if c.image(Side.A) is None else c.image(Side.A).adjustments.enhance_mode
        enhance_b = None if c.image(Side.B) is None else c.image(Side.B).adjustments.enhance_mode
        return {
            "active_primary_tool": c.active_tool,
            "overlay_mode": d.overlay_mode,
            "overlay_on": self._w._overlay_workspace or overlay_is_registered(d.overlay_mode),
            "sync_state": bool(c.sync_zoom or c.sync_pan),
            "mesh_visibility": mesh_on,
            "enhancement_mode": (enhance_a, enhance_b),
            "magnifier_mode": (bool(d.magnifier_on), int(c.magnifier_factor)),
            "difference_mode": d.overlay_mode in (OverlayMode.DIFFERENCE, OverlayMode.EDGE_DIFFERENCE),
            "skeleton_mode": d.mesh.view_mode == MeshViewMode.SKELETON.value,
        }


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PCB Image Comparator")
        self.setDockNestingEnabled(True)
        self.setDockOptions(
            QMainWindow.DockOption.AnimatedDocks
            | QMainWindow.DockOption.AllowTabbedDocks
            | QMainWindow.DockOption.AllowNestedDocks
        )
        from ui.theme import apply_dark

        app = QApplication.instance()
        if app is not None and not app.styleSheet():
            try:
                apply_dark(app)
            except Exception:
                pass
        self.controller = ProjectController()
        self.modes = ToolModeManager(self)
        self.tool_clicks: list[tuple[Side, float, float]] = []
        self._crop_draft = None
        self._crop_handle = None
        self._crop_move = None
        self._crop_target: Side | None = None
        self._crop_state = {Side.A: "CROP_OFF", Side.B: "CROP_OFF"}
        self._selection: list[tuple] = []
        self._syncing_manager = False
        self._selection_pulse = False
        self._pulse_left = 0
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(120)
        self._pulse_timer.timeout.connect(self._pulse_tick)
        self._hover = None
        self._hit_cycle: list[tuple] = []
        self._hit_cycle_i = 0
        self._moving = None
        self._dim_orientation = "ALIGNED"
        self._contour_press = None
        self._contour_edit = None
        self.pending_snap = None
        self._space_down = False
        self._flicker_toggle = False
        self._docks_hidden = False
        self._saved_dock_state = None
        self.docks: dict[str, QDockWidget] = {}
        self._all_actions: list[QAction] = []

        self.view_a = ImageView(Side.A)
        self.view_b = ImageView(Side.B)
        self.view_a.crop_hud.keep_btn.clicked.connect(lambda: self._apply_crop(Side.A))
        self.view_b.crop_hud.keep_btn.clicked.connect(lambda: self._apply_crop(Side.B))
        self.view_a.crop_hud.cancel_btn.clicked.connect(lambda: self._cancel_crop(Side.A))
        self.view_b.crop_hud.cancel_btn.clicked.connect(lambda: self._cancel_crop(Side.B))
        self.view_a.crop_hud.reset_btn.clicked.connect(lambda: self._reset_crop(Side.A))
        self.view_b.crop_hud.reset_btn.clicked.connect(lambda: self._reset_crop(Side.B))
        self.pane_a = ViewerPane(Side.A, self.view_a)
        self.pane_b = ViewerPane(Side.B, self.view_b)
        self._syncing = False

        self._size_to_screen()
        self._build_central()
        self._install_undo()
        self.toolbar = MainToolBar(self)
        self.addToolBar(self.toolbar)
        self._all_actions.extend(a for a in self.toolbar.actions() if a.text())
        self._build_menus()
        self._build_docks()
        self._build_status()
        self._connect()
        self._apply_or_restore_layout()
        self._autosave = QTimer(self)
        self._autosave.setInterval(60_000)
        self._autosave.timeout.connect(self.controller.autosave_recovery)
        self._autosave.start()
        self._flicker_timer = QTimer(self)
        self._flicker_timer.timeout.connect(self._auto_flicker)
        self.set_tool(ActiveTool.PAN)
        self.refresh_all()
        self._maybe_first_run()

    def _size_to_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(1100, 700)
            return
        avail = screen.availableGeometry()
        width = min(1100, max(800, avail.width() - 48))
        height = min(700, max(540, avail.height() - 48))
        self.resize(width, height)
        self.setMinimumSize(640, 480)

    def _install_undo(self) -> None:
        """One Undo/Redo pair registered on the window. Keyboard routing is global."""
        stack = self.controller.undo_stack
        self.act_undo = stack.createUndoAction(self, "Undo")
        self.act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        self.act_undo.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.act_redo = stack.createRedoAction(self, "Redo")
        self.act_redo.setShortcuts([QKeySequence.StandardKey.Redo, QKeySequence("Ctrl+Shift+Z")])
        self.act_redo.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.addAction(self.act_undo)
        self.addAction(self.act_redo)
        self._all_actions.extend([self.act_undo, self.act_redo])
        app = QApplication.instance()
        self._undo_filter = ProjectUndoFilter(stack, self)
        if app is not None:
            app.installEventFilter(self._undo_filter)
            app.focusChanged.connect(self._undo_focus_changed)

    def _undo_focus_changed(self, _old, new) -> None:
        live = is_live_text_editor(new)
        if live:
            self.act_undo.setShortcuts([])
            self.act_redo.setShortcuts([])
        else:
            self.act_undo.setShortcut(QKeySequence.StandardKey.Undo)
            self.act_redo.setShortcuts([QKeySequence.StandardKey.Redo, QKeySequence("Ctrl+Shift+Z")])

    def _build_central(self) -> None:
        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.workflow = WorkflowBar()
        self.workflow.setMaximumHeight(28)
        v.addWidget(self.workflow)
        self.workspace_split = QSplitter(Qt.Orientation.Horizontal)
        self.workspace_split.setChildrenCollapsible(False)
        self.workspace_split.addWidget(self.pane_a)
        self.workspace_split.addWidget(self.pane_b)
        self.workspace_split.setStretchFactor(0, 1)
        self.workspace_split.setStretchFactor(1, 1)
        v.addWidget(self.workspace_split, 1)
        self.overlay_bar = OverlayBar()
        self.overlay_bar.hide()
        self.overlay_bar.opacityChanged.connect(self._overlay_opacity)
        self.overlay_bar.preset.connect(self._overlay_preset)
        self.overlay_bar.closed.connect(self.close_overlay)
        self.overlay_bar.directionChanged.connect(self._set_overlay_direction)
        v.addWidget(self.overlay_bar)
        self._overlay_workspace = False
        self._last_overlay_mode = OverlayMode.B_OVER_A
        self._last_mesh_mode = MeshViewMode.GHOST
        self._last_diff_mode = OverlayMode.A_ONLY
        self.setCentralWidget(central)

    def _act(self, menu, text: str, slot, shortcut: str | None = None, *, checkable: bool = False, enabled: bool = True, tip: str | None = None) -> QAction:
        action = QAction(text, self)
        action.setProperty("en_text", text)
        action.setEnabled(enabled)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        if checkable:
            action.setCheckable(True)
        if tip:
            action.setToolTip(tip)
            action.setStatusTip(tip)
        if not enabled and tip:
            action.setToolTip(tip)
        if enabled and slot is not None:
            action.triggered.connect(slot)
        menu.addAction(action)
        self._all_actions.append(action)
        return action

    def _build_menus(self) -> None:
        file_m = self.menuBar().addMenu("&File")
        self._act(file_m, "New Project", self._new, "Ctrl+N")
        self._act(file_m, "Open Project...", self._open, "Ctrl+O")
        self._act(file_m, "Save Project", self._save, "Ctrl+S")
        self._act(file_m, "Save Project As...", self._save_as, "Ctrl+Shift+S")
        file_m.addSeparator()
        self._act(file_m, "Open Image A...", lambda: self._load_image(Side.A))
        self._act(file_m, "Open Image B...", lambda: self._load_image(Side.B))
        self._act(file_m, "Remove Image A", lambda: self._remove_image(Side.A))
        self._act(file_m, "Remove Image B", lambda: self._remove_image(Side.B))
        file_m.addSeparator()
        self._act(file_m, "Import Vector...", self._import_vector)
        file_m.addSeparator()
        exp = file_m.addMenu("Export")
        self._act(exp, "Export Overlay PNG...", lambda: self._export_view("overlay"))
        self._act(exp, "Export Difference PNG...", lambda: self._export_view("difference"))
        self._act(exp, "Export Annotated Image...", self._export_annotated)
        self._act(exp, "Export Geometry Map PNG...", lambda: self._export_geometry_map())
        self._act(exp, "Export Points CSV...", self._export_points)
        self._act(exp, "Export Measurements CSV...", self._export_points)
        self._act(exp, "Export Mesh Edges CSV...", self._export_mesh_edges)
        self._act(exp, "Export Mesh Triangles CSV...", self._export_mesh_triangles)
        self._act(exp, "Export Contours CSV...", self._export_contours)
        self._act(exp, "Export PDF Report...", self._pdf)
        file_m.addSeparator()
        self.recent_menu = file_m.addMenu("Recent Projects")
        self._rebuild_recent_menu()
        file_m.addSeparator()
        self._act(file_m, "Exit", self.close)

        edit = self.menuBar().addMenu("&Edit")
        edit.addAction(self.act_undo)
        edit.addAction(self.act_redo)
        self._act(edit, "Undo History", self._undo_history)
        edit.addSeparator()
        self._act(edit, "Paste Image to A", lambda: self.paste_image(Side.A), "Ctrl+Shift+V")
        self._act(edit, "Paste Image to B", lambda: self.paste_image(Side.B), "Ctrl+Alt+V")
        self._act(edit, "Paste Image…", self.paste_image_smart, "Ctrl+V")
        edit.addSeparator()
        self._act(edit, "Delete Selected", self._delete_selected, "Delete")
        self._act(edit, "Hide Selected", self._hide_selected)
        self._act(edit, "Show Hidden", self._show_hidden)
        self._act(edit, "Reset Actual", self._reset_actual_selected)
        self._act(edit, "Select All Points", self._select_all_points, "Ctrl+A")
        edit.addSeparator()
        self._act(edit, "Clear All CONTROL Points...", lambda: self._clear_kind("control"))
        self._act(edit, "Clear All Dimensions...", lambda: self._clear_kind("dimensions"))
        self._act(edit, "Clear All Contours...", lambda: self._clear_kind("contours"))
        self._act(edit, "Clear Mesh...", self._clear_mesh_confirm)
        self._act(edit, "Clear Measurements...", lambda: self._clear_kind("measurements"))
        edit.addSeparator()
        self._act(edit, "Preferences", self._preferences)

        view = self.menuBar().addMenu("&View")
        self._act(view, "Fit A", self.view_a.fit_image)
        self._act(view, "Fit B", self.view_b.fit_image)
        self._act(view, "Fit Both", self.fit_both, "F")
        self._act(view, "Actual Size A", self.view_a.zoom_100, "1")
        self._act(view, "Actual Size B", self.view_b.zoom_100)
        self._act(view, "SOURCE 1:1", self.actual_size_both)
        self._act(view, "Reset View", self.reset_views)
        self.act_precision = self._act(view, "Precision Drawing Mode", self._toggle_precision, checkable=True)
        self.act_precision_points = self._act(view, "Precision Point Display", self._toggle_precision_points, checkable=True)
        self.act_precision_points.setChecked(bool(self.controller.data.precision_points))
        self.act_magnifier = self._act(view, "Magnifier", self._toggle_magnifier, checkable=True)
        self.act_magnifier.setChecked(bool(self.controller.data.magnifier_on))
        mag = view.addMenu("Magnifier")
        mag.menuAction().setProperty("en_text", "Magnifier")
        self.act_mag_off = self._act(mag, "Magnifier Off", lambda: self._set_magnifier_mode(0))
        self.act_mag_5 = mag.addAction("5x")
        self.act_mag_5.triggered.connect(lambda: self._set_magnifier_mode(5))
        self.act_mag_10 = mag.addAction("10x")
        self.act_mag_10.triggered.connect(lambda: self._set_magnifier_mode(10))
        self.act_mag_20 = mag.addAction("20x")
        self.act_mag_20.triggered.connect(lambda: self._set_magnifier_mode(20))
        self._act(view, "200%", lambda: self._zoom_both(2.0))
        self._act(view, "400%", lambda: self._zoom_both(4.0))
        self._act(view, "800%", lambda: self._zoom_both(8.0))
        self._act(view, "1600%", lambda: self._zoom_both(16.0))
        self.act_smooth = self._act(view, "Zoom: SMOOTH", lambda: self._set_pixel_precise(False), checkable=True)
        self.act_pixel = self._act(view, "Zoom: PIXEL-PRECISE", lambda: self._set_pixel_precise(True), checkable=True)
        self.act_smooth.setChecked(True)
        view.addSeparator()
        self._act(view, "A Only", lambda: self.set_workspace("A"))
        self._act(view, "B Only", lambda: self.set_workspace("B"))
        self._act(view, "Split A/B", lambda: self.set_workspace("split"))
        self.act_ov_view_off = self._act(view, "Overlay Off", self.close_overlay)
        self.act_ov_ab = self._act(view, "A over B", lambda: self.show_overlay_mode(OverlayMode.A_OVER_B))
        self.act_ov_ba = self._act(view, "B over A", lambda: self.show_overlay_mode(OverlayMode.B_OVER_A))
        self.act_diff = self._act(view, "Difference", lambda: self.set_overlay(OverlayMode.DIFFERENCE), checkable=True)
        self.act_edge = self._act(view, "Edge Difference", lambda: self.set_overlay(OverlayMode.EDGE_DIFFERENCE), checkable=True)
        self.act_flicker = self._act(view, "Flicker", lambda: self.set_overlay(OverlayMode.FLICKER), checkable=True)
        view.addSeparator()
        self._act(view, "Display Mirror A Horizontally", lambda: self.display_mirror(Side.A, "x"))
        self._act(view, "Display Mirror A Vertically", lambda: self.display_mirror(Side.A, "y"))
        self._act(view, "Display Mirror B Horizontally", lambda: self.display_mirror(Side.B, "x"))
        self._act(view, "Display Mirror B Vertically", lambda: self.display_mirror(Side.B, "y"))
        view.addSeparator()
        self.act_advanced = self._act(view, "Advanced Mode", self._toggle_advanced, checkable=True)
        view.addSeparator()
        vis = view.addMenu("Visibility")
        self.act_vis_zero = self._act(vis, "ZERO/SCALE", lambda: self._toggle_data_flag("show_zero_scale"), checkable=True)
        self.act_show_points = self._act(vis, "CONTROL Points", lambda: self._toggle_mesh_flag("show_points"), checkable=True)
        self.act_show_ids = self._act(vis, "Show Point IDs", self._toggle_show_ids, checkable=True)
        self.act_vis_expected = self._act(vis, "Expected Points", lambda: self._toggle_mesh_flag("show_expected"), checkable=True)
        self.act_vis_actual = self._act(vis, "Actual Points", lambda: self._toggle_mesh_flag("show_actual"), checkable=True)
        self.act_vis_vectors = self._act(vis, "Residual Vectors", lambda: self._toggle_mesh_flag("show_vectors"), checkable=True)
        self.act_vis_mesh = self._act(vis, "Mesh", self._toggle_mesh, checkable=True)
        self.act_vis_dims = self._act(vis, "Dimensions", lambda: self._toggle_data_flag("show_dimensions"), checkable=True)
        self.act_vis_contours = self._act(vis, "Contours", lambda: self._toggle_data_flag("show_contours"), checkable=True)
        self.act_vis_meas = self._act(vis, "Measurements", lambda: self._toggle_data_flag("show_measurements"), checkable=True)
        vis.addSeparator()
        self._act(vis, "Show Mesh Overlay", lambda: self._set_mesh_mode(MeshViewMode.MESH))
        self._act(vis, "Show Expected Mesh", lambda: self._set_mesh_mode(MeshViewMode.EXPECTED))
        self._act(vis, "Show Actual Mesh", lambda: self._set_mesh_mode(MeshViewMode.ACTUAL))
        self._act(vis, "Show Error Map", lambda: self._set_mesh_mode(MeshViewMode.ERROR_MAP))
        self._sync_vis_actions()
        view.addSeparator()
        self.act_sync_zoom = self._act(view, "Sync Zoom", None, checkable=True)
        self.act_sync_zoom.toggled.connect(lambda v: setattr(self.controller, "sync_zoom", v))
        self.act_sync_pan = self._act(view, "Sync Pan", None, checkable=True)
        self.act_sync_pan.toggled.connect(lambda v: setattr(self.controller, "sync_pan", v))
        view.addSeparator()
        self._act(view, "Image Workspace", self.image_workspace, "Tab")
        self._act(view, "Reset Layout", self.reset_layout)
        self._act(view, "Full Screen", self.toggle_fullscreen, "F11")
        panels = view.addMenu("Panels")
        self._panel_actions = {}
        for key, title in (
            ("points", "Points"),
            ("groups", "Groups"),
            ("measurements", "Measurements"),
            ("contours", "Contours"),
            ("layers", "Layers"),
            ("registration", "Registration"),
            ("calibration", "Calibration"),
            ("transform", "Transform Inspector"),
            ("statistics", "Statistics"),
            ("width", "Width Profile"),
            ("notes", "Notes / Audit"),
            ("overlay", "Overlay"),
            ("adjustments", "Image Adjustments"),
            ("session", "Session"),
        ):
            self._panel_actions[key] = self._act(panels, title, lambda checked=False, k=key: self.show_dock(k))

        reg = self.menuBar().addMenu("&Registration")
        self._act(reg, "Add Reference Point", lambda: self.set_tool(ActiveTool.REFERENCE), "R")
        self._act(reg, "Translation Only", lambda: self._quick_fit(TransformMode.TRANSLATION))
        self._act(reg, "Rigid", lambda: self._quick_fit(TransformMode.RIGID))
        self._act(reg, "Similarity", lambda: self._quick_fit(TransformMode.SIMILARITY))
        self._act(reg, "Test Orientations", self._orientations)
        reg.addSeparator()
        self._act(reg, "Mirror A Horizontally", lambda: self.mirror_image(Side.A, "x"))
        self._act(reg, "Mirror A Vertically", lambda: self.mirror_image(Side.A, "y"))
        self._act(reg, "Mirror B Horizontally", lambda: self.mirror_image(Side.B, "x"))
        self._act(reg, "Mirror B Vertically", lambda: self.mirror_image(Side.B, "y"))
        self._act(reg, "Reset A Orientation", lambda: self.reset_orient(Side.A))
        self._act(reg, "Reset B Orientation", lambda: self.reset_orient(Side.B))
        reg.addSeparator()
        self._act(reg, "Normal", lambda: self._set_orient(OrientationName.NORMAL))
        self._act(reg, "Mirror X", lambda: self._set_orient(OrientationName.MIRROR_X))
        self._act(reg, "Mirror Y", lambda: self._set_orient(OrientationName.MIRROR_Y))
        self._act(reg, "Rotate 90°", lambda: self._set_orient(OrientationName.ROTATE_90))
        self._act(reg, "Rotate 180°", lambda: self._set_orient(OrientationName.ROTATE_180))
        self._act(reg, "Rotate 270°", lambda: self._set_orient(OrientationName.ROTATE_270))
        reg.addSeparator()
        self._act(reg, "Review Transform", self._review_transform)
        self._act(reg, "Accept Transform", self._accept_only)
        self._act(reg, "Accept Alignment", self._accept_alignment)
        self._act(reg, "Lock Alignment", self._lock)
        self._act(reg, "Unlock Alignment", self._unlock)
        self._act(reg, "Reset Alignment", self._reset_alignment)
        self._act(reg, "Transform History", lambda: self.show_dock("transform"))

        mesh_m = self.menuBar().addMenu("Mes&h")
        self._act(mesh_m, "Build Mesh", self._build_mesh)
        self._act(mesh_m, "Show/Hide Mesh", self._toggle_mesh)
        self._act(mesh_m, "Points Only", lambda: self._set_mesh_mode(MeshViewMode.POINTS_ONLY))
        self._act(mesh_m, "Mesh + Vectors", lambda: self._set_mesh_mode(MeshViewMode.MESH_VECTORS))
        self._act(mesh_m, "Expected Mesh", lambda: self._set_mesh_mode(MeshViewMode.EXPECTED))
        self._act(mesh_m, "Actual Mesh", lambda: self._set_mesh_mode(MeshViewMode.ACTUAL))
        self._act(mesh_m, "Both / Ghost Mesh", lambda: self._set_mesh_mode(MeshViewMode.GHOST))
        self._act(mesh_m, "Error Map", lambda: self._set_mesh_mode(MeshViewMode.ERROR_MAP))
        self._act(mesh_m, "Local Scale", lambda: self._set_mesh_mode(MeshViewMode.LOCAL_SCALE))
        self._act(mesh_m, "Local Rotation", lambda: self._set_mesh_mode(MeshViewMode.LOCAL_ROTATION))
        self._act(mesh_m, "Geometric Stretch", lambda: self._set_mesh_mode(MeshViewMode.STRETCH))
        self._act(mesh_m, "Skeleton View", self._toggle_skeleton)
        mesh_m.addSeparator()
        self._act(mesh_m, "Add Edge", lambda: self._mesh_edit("add"))
        self._act(mesh_m, "Remove Edge", lambda: self._mesh_edit("remove"))
        self._act(mesh_m, "Lock Edge", lambda: self._mesh_edit("lock"))
        self._act(mesh_m, "Clear Mesh...", self._clear_mesh_confirm)
        mesh_m.addSeparator()
        self._act(mesh_m, "Global Mesh", lambda: self._build_mesh(None))
        for gname in ("BOARD", "KEYPAD", "LCD", "BATTERY", "MOUNTING", "CUSTOM"):
            self._act(mesh_m, f"{gname} Mesh", lambda checked=False, n=gname: self._build_mesh(n))

        meas = self.menuBar().addMenu("&Measure")
        self._act(meas, "CALCULATE", self._calculate)
        self._act(meas, "LOG", self._show_log, "Ctrl+L")
        self._act(meas, "Set Tolerance…", self._set_tolerance)
        meas.addSeparator()
        self._act(meas, "Control Point", lambda: self.set_tool(ActiveTool.CONTROL))
        self._act(meas, "Measurement Point", lambda: self.set_tool(ActiveTool.MEASUREMENT), "M")
        self._act(meas, "Dimension", lambda: self.set_tool(ActiveTool.DIMENSION), "D")
        self._act(meas, "Contour", lambda: self.set_tool(ActiveTool.CONTOUR))
        self._act(meas, "Crop", lambda: self.set_tool(ActiveTool.CROP))
        self._act(meas, "Project last contour A→B", self._project_last_contour)
        self._act(meas, "Show last dimension on other side", self._project_last_dimension)
        self._act(meas, "Edit last dimension…", self._edit_last_dimension)
        self._act(meas, "Distance (legacy)", lambda: self.set_tool(ActiveTool.DISTANCE))
        self._act(meas, "Angle", lambda: self.set_tool(ActiveTool.ANGLE))
        self._act(meas, "Circle / Hole", lambda: self.set_tool(ActiveTool.CIRCLE))
        self._act(meas, "Pitch", lambda: self.set_tool(ActiveTool.PITCH))
        self._act(meas, "Cross-section", lambda: self.set_tool(ActiveTool.CROSS_SECTION))
        self._act(meas, "Set Zero", lambda: self.set_tool(ActiveTool.ZERO))
        self._act(meas, "Registration Reference", lambda: self.set_tool(ActiveTool.SCALE))
        meas.addSeparator()
        self._act(meas, "Calibrate Image A", lambda: self._calibrate_side(Side.A))
        self._act(meas, "Calibrate Image B", lambda: self._calibrate_side(Side.B))
        self._act(meas, "Clear Measurement Tool", lambda: self.set_tool(ActiveTool.PAN), "Esc")

        pcb = self.menuBar().addMenu("&PCB")
        self._act(pcb, "Compare Board Outlines", self._compare_outlines)
        self._act(pcb, "Compare Contact Group", self._compare_group)
        self._act(pcb, "Front / Back Mode", self._front_back_dialog)
        self._act(pcb, "IMPORT PCB / CAD…", self._import_pcb_cad)
        self._act(pcb, "Import Vector (DXF/SVG)…", self._import_vector)
        self._act(pcb, "Layer Manager", lambda: self.show_dock("layers"))
        self._act(pcb, "Board Coordinate System", self._board_cs)

        tools = self.menuBar().addMenu("&Tools")
        self._act(tools, "Calibration Manager", lambda: self.show_dock("calibration"))
        self._act(tools, "Transform Inspector", lambda: self.show_dock("transform"))
        self._act(tools, "Point Manager", lambda: self.show_dock("points"))
        self._act(tools, "Object Manager", lambda: self.show_dock("points"))
        self._act(tools, "Group Manager", lambda: self.show_dock("groups"))
        self._act(tools, "Contour Manager", lambda: self.show_dock("contours"))
        self._act(tools, "Tolerance Settings", self._tolerances)
        self._act(tools, "Image Adjustments", lambda: self.show_dock("adjustments"))
        self._act(tools, "Image Information", self._image_info)
        self._act(tools, "Statistics", lambda: self.show_dock("statistics"))
        self._act(tools, "Audit Log", self._show_audit)
        self._act(tools, "Preferences", self._preferences)
        self._act(tools, "QUALITY TEST", self._quality_test)
        tools.addSeparator()
        self._act(tools, "Propose Edge A (must accept)", lambda: self.controller.propose_edge(Side.A))
        self._act(tools, "Propose Edge B (must accept)", lambda: self.controller.propose_edge(Side.B))
        self._act(tools, "Automatic Width Sections", self._auto_sections)
        self._act(tools, "Perspective Correction", self._perspective)
        self._act(tools, "Run Diagnostics", self._show_diagnostics)

        help_m = self.menuBar().addMenu("&Help")
        self._act(help_m, "Quick Start", self._quick_start)
        self._act(help_m, "Keyboard Shortcuts", self._shortcuts_help)
        self._act(help_m, "Image Information", self._image_info)
        self._act(help_m, "About PCB Image Comparator", self._about)

    def _build_docks(self) -> None:
        self.point_table = PointTable(self.controller)
        self.group_panel = GroupPanel(self.controller)
        self.meas_panel = MeasurementPanel(self.controller)
        self.contour_panel = ContourPanel(self.controller)
        self.inspector = TransformInspector(self.controller)
        self.summary = SessionSummary(self.controller)
        self.overlay = OverlayControls(self.controller)
        self.layers = LayerPanel(self.controller)
        self.width_plot = WidthProfileWidget()
        self.cal_panel = _CalibrationDock(self)
        self.notes_panel = _NotesDock(self.controller)
        from ui.adjustments_panel import ImageAdjustmentsPanel

        self.adjust_panel = ImageAdjustmentsPanel(self.controller)

        specs = (
            ("points", "Object Manager", self.point_table, Qt.DockWidgetArea.RightDockWidgetArea),
            ("groups", "Groups", self.group_panel, Qt.DockWidgetArea.RightDockWidgetArea),
            ("measurements", "Measurements", self.meas_panel, Qt.DockWidgetArea.RightDockWidgetArea),
            ("contours", "Contours", self.contour_panel, Qt.DockWidgetArea.RightDockWidgetArea),
            ("layers", "Layers", self.layers, Qt.DockWidgetArea.RightDockWidgetArea),
            ("transform", "Transform Inspector", self.inspector, Qt.DockWidgetArea.RightDockWidgetArea),
            ("calibration", "Calibration", self.cal_panel, Qt.DockWidgetArea.RightDockWidgetArea),
            ("width", "Width Profile", self.width_plot, Qt.DockWidgetArea.BottomDockWidgetArea),
            ("notes", "Notes / Audit", self.notes_panel, Qt.DockWidgetArea.RightDockWidgetArea),
            ("overlay", "Overlay", self.overlay, Qt.DockWidgetArea.RightDockWidgetArea),
            ("adjustments", "Image Adjustments", self.adjust_panel, Qt.DockWidgetArea.RightDockWidgetArea),
            ("session", "Session", self.summary, Qt.DockWidgetArea.RightDockWidgetArea),
        )
        for key, title, widget, area in specs:
            self._add_dock(key, title, widget, area)
        self.docks["statistics"] = self.docks["measurements"]
        self.docks["registration"] = self.docks["transform"]

    def _add_dock(self, key: str, title: str, widget: QWidget, area) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(widget)
        dock = QDockWidget(title, self)
        dock.setProperty("en_title", title)
        dock.setObjectName(f"dock_{key}")
        dock.setWidget(scroll)
        dock.setMinimumWidth(200)
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.addDockWidget(area, dock)
        dock.hide()
        self.docks[key] = dock
        # statistics shares measurements widget — skip second wrap if already parented
        _ = title

    def _build_status(self) -> None:
        bar = QStatusBar()
        self.setStatusBar(bar)
        self.lock_label = QLabel("")
        self.convention = QLabel("")
        self.st_a_px = QLabel("A px: —")
        self.st_a_ph = QLabel("A phys: —")
        self.st_b_px = QLabel("B px: —")
        self.st_b_ph = QLabel("B phys: —")
        self.st_zoom = QLabel("zoom: —")
        self.st_tool = QLabel("tool: PAN")
        self.st_modes = QLabel("")
        self.st_reg = QLabel("Alignment: none")
        self.st_scale = QLabel("Scale —")
        self.st_rot = QLabel("Rotation —")
        bar.addWidget(self.lock_label)
        bar.addWidget(self.convention, 1)
        for w in (
            self.st_a_px,
            self.st_a_ph,
            self.st_b_px,
            self.st_b_ph,
            self.st_scale,
            self.st_rot,
            self.st_zoom,
            self.st_tool,
            self.st_modes,
            self.st_reg,
        ):
            bar.addPermanentWidget(w)
        self._last_cursor = {Side.A: (None, None), Side.B: (None, None)}

    def _connect(self) -> None:
        c = self.controller
        c.imagesChanged.connect(self.refresh_images)
        c.pointsChanged.connect(self.refresh_overlays)
        c.transformChanged.connect(self.refresh_all)
        c.contoursChanged.connect(self.refresh_overlays)
        c.changed.connect(self.refresh_chrome)
        c.statusMessage.connect(self.statusBar().showMessage)
        c.errorMessage.connect(self._on_controller_error)
        self.view_a.pointClicked.connect(lambda x, y: self._click(Side.A, x, y))
        self.view_b.pointClicked.connect(lambda x, y: self._click(Side.B, x, y))
        self.view_a.previewMoved.connect(lambda x, y: self._preview_move(Side.A, x, y))
        self.view_b.previewMoved.connect(lambda x, y: self._preview_move(Side.B, x, y))
        self.view_a.releasedAt.connect(lambda x, y, d: self._released(Side.A, x, y, d))
        self.view_b.releasedAt.connect(lambda x, y, d: self._released(Side.B, x, y, d))
        self.view_a.dragMoved.connect(lambda x0, y0, x1, y1: self._drag_move(Side.A, x0, y0, x1, y1))
        self.view_b.dragMoved.connect(lambda x0, y0, x1, y1: self._drag_move(Side.B, x0, y0, x1, y1))
        self.view_a.doubleClickedAt.connect(lambda x, y: self._double_click(Side.A, x, y))
        self.view_b.doubleClickedAt.connect(lambda x, y: self._double_click(Side.B, x, y))
        from app.i18n import broker

        broker().languageChanged.connect(lambda *_: self.retranslate_ui())
        self.view_a.cursorMoved.connect(lambda x, y: self._cursor(Side.A, x, y))
        self.view_b.cursorMoved.connect(lambda x, y: self._cursor(Side.B, x, y))
        self.view_a.viewChanged.connect(lambda: self._sync_from(self.view_a, self.view_b))
        self.view_b.viewChanged.connect(lambda: self._sync_from(self.view_b, self.view_a))
        self.point_table.zoomTo.connect(self._manager_double_click)
        self.point_table.changed.connect(self.refresh_overlays)
        self.point_table.centerA.connect(lambda item: self._center_item(item, sides=(Side.A,)))
        self.point_table.centerB.connect(lambda item: self._center_item(item, sides=(Side.B,)))
        self.point_table.centerBoth.connect(lambda item: self._center_item(item, sides=(Side.A, Side.B)))
        self.point_table.resetActual.connect(self._reset_actual_id)
        self.point_table.hideIds.connect(self._hide_ids)
        self.point_table.managerSelect.connect(self._manager_select)
        self.point_table.showObject.connect(self._show_object)
        self.point_table.actionDelete.connect(self._delete_selected)
        self.overlay.changed.connect(self.refresh_images)
        self.summary.navigate.connect(self._navigate)
        self.view_a.requestNudge.connect(lambda dx, dy: self._nudge(Side.A, dx, dy))
        self.view_b.requestNudge.connect(lambda dx, dy: self._nudge(Side.B, dx, dy))
        self.pane_a.openRequested.connect(lambda: self._load_image(Side.A))
        self.pane_b.openRequested.connect(lambda: self._load_image(Side.B))
        self.pane_a.pasteRequested.connect(lambda: self.paste_image(Side.A))
        self.pane_b.pasteRequested.connect(lambda: self.paste_image(Side.B))
        self.pane_a.filesDropped.connect(lambda paths: self._drop_files(Side.A, paths))
        self.pane_b.filesDropped.connect(lambda paths: self._drop_files(Side.B, paths))
        self.pane_a.imageDropped.connect(lambda img: self._drop_qimage(Side.A, img))
        self.pane_b.imageDropped.connect(lambda img: self._drop_qimage(Side.B, img))
        self.pane_a.replaceRequested.connect(lambda: self._load_image(Side.A))
        self.pane_b.replaceRequested.connect(lambda: self._load_image(Side.B))
        self.pane_a.removeRequested.connect(lambda: self._remove_image(Side.A))
        self.pane_b.removeRequested.connect(lambda: self._remove_image(Side.B))
        self.view_a.filesDropped.connect(lambda paths: self._drop_files(Side.A, paths))
        self.view_b.filesDropped.connect(lambda paths: self._drop_files(Side.B, paths))
        self.view_a.imageDropped.connect(lambda img: self._drop_qimage(Side.A, img))
        self.view_b.imageDropped.connect(lambda img: self._drop_qimage(Side.B, img))
        self.view_a.contextCommand.connect(lambda cmd, x, y: self._context(Side.A, cmd, x, y))
        self.view_b.contextCommand.connect(lambda cmd, x, y: self._context(Side.B, cmd, x, y))
        self.view_a.menuAugment = lambda menu, x, y: self._augment_view_menu(Side.A, menu, x, y)
        self.view_b.menuAugment = lambda menu, x, y: self._augment_view_menu(Side.B, menu, x, y)
        c.calibrationReady.connect(self._on_scale_clicks_ready)

    def _on_controller_error(self, msg: str) -> None:
        if _offscreen():
            if self.statusBar() is not None:
                self.statusBar().showMessage(msg)
            return
        QMessageBox.warning(self, "PCB Image Comparator", msg)

    # ----- tools -----
    def _tool_clicked(self, tool: ActiveTool) -> None:
        if self.controller.active_tool == tool and tool != ActiveTool.PAN:
            self.set_tool(ActiveTool.PAN)
            return
        self.set_tool(tool)

    def set_tool(self, tool: ActiveTool) -> None:
        prev = self.controller.active_tool
        if tool != prev:
            self._cancel_unfinished(keep_tool=True)
        self.controller.active_tool = tool
        self.tool_clicks.clear()
        placing = tool not in (ActiveTool.PAN, ActiveTool.SELECT)
        self.view_a.set_placing(placing)
        self.view_b.set_placing(placing)
        if hasattr(self, "toolbar"):
            group = getattr(self.toolbar, "_tool_group", None)
            if group is not None:
                group.setExclusive(False)
            for t, act in getattr(self.toolbar, "actions_by_tool", {}).items():
                act.setChecked(t == tool and tool != ActiveTool.PAN)
        from app.i18n import tr

        label = tr("tool.nav") if tool == ActiveTool.PAN else tool.value
        self.st_tool.setText(f"{tr('st.tool')}: {label}")
        tips = {
            ActiveTool.ZERO: tr("tip.zero"),
            ActiveTool.SCALE: tr("tip.scale"),
            ActiveTool.CONTROL: tr("tip.control"),
            ActiveTool.DIMENSION: tr("status.dim_p1"),
            ActiveTool.CONTOUR: tr("status.contour"),
            ActiveTool.CROP: tr("status.crop"),
            ActiveTool.SELECT: tr("tip.select"),
        }
        self.statusBar().showMessage(tips.get(tool, f"{tr('st.tool')}: {label}"))
        if hasattr(self, "toolbar"):
            self._refresh_crop_chrome()
        if tool != ActiveTool.CROP:
            self.view_a.clear_preview()
            self.view_b.clear_preview()
            if prev == ActiveTool.CROP:
                self.view_a.hide_crop_hud()
                self.view_b.hide_crop_hud()
        else:
            self.statusBar().showMessage(tr("status.crop"))
        self._refresh_mode_ui()

    def _cancel_unfinished(self, *, keep_tool: bool = False) -> bool:
        """Cancel in-progress input. Does not delete completed geometry."""
        cancelled = False
        if getattr(self.controller, "mesh_edit", None) or getattr(self.controller, "_mesh_pick", None):
            self.controller.mesh_edit = None
            self.controller._mesh_pick = None
            cancelled = True
        if self._crop_draft is not None or self._crop_handle is not None or self._crop_move is not None:
            self._crop_draft = None
            self._crop_handle = None
            self._crop_move = None
            self.view_a.clear_preview()
            self.view_b.clear_preview()
            self.view_a.hide_crop_hud()
            self.view_b.hide_crop_hud()
            cancelled = True
        if self.tool_clicks:
            self.tool_clicks.clear()
            self.view_a.clear_preview()
            self.view_b.clear_preview()
            cancelled = True
        if getattr(self, "_contour_press", None) or getattr(self, "_contour_edit", None):
            self._contour_press = None
            self._contour_edit = None
            cancelled = True
        _ = keep_tool
        return cancelled

    def set_snap(self, mode: SnapMode) -> None:
        self.controller.snap_mode = mode
        self.statusBar().showMessage(f"Snap: {mode.value} (proposals must be confirmed)")

    def _set_mag(self, factor: int) -> None:
        self.controller.magnifier_factor = int(factor)
        self.view_a.magnifier.set_factor(int(factor))
        self.view_b.magnifier.set_factor(int(factor))

    def _set_magnifier_mode(self, factor: int) -> None:
        if factor is None or int(factor) <= 0:
            self.controller.data.magnifier_on = False
        else:
            self.controller.data.magnifier_on = True
            self.controller.magnifier_factor = int(factor)
            self.view_a.magnifier.set_factor(int(factor))
            self.view_b.magnifier.set_factor(int(factor))
        if hasattr(self, "act_magnifier"):
            self.act_magnifier.setChecked(bool(self.controller.data.magnifier_on))
        self._apply_appearance()
        self._refresh_mode_ui()

    def _click(self, side: Side, x: float, y: float) -> None:
        if self.controller.active_tool == ActiveTool.CROP:
            self._crop_press(side, x, y)
            return
        x, y = self.controller.unview_xy(side, x, y)
        img = self.controller.image(side)
        array = self.controller.display_array(side)
        mode = self.controller.snap_mode or SnapMode.NONE
        if mode != SnapMode.NONE and array is not None:
            prop = propose_snap(array, x, y, mode)
            if prop is not None:
                if QMessageBox.question(
                    self,
                    "Confirm snap",
                    f"{prop.description}\nProposed: ({prop.x:.3f}, {prop.y:.3f})\nConfirm this position?",
                ) != QMessageBox.StandardButton.Yes:
                    return
                x, y = prop.x, prop.y
        tool = self.controller.active_tool
        if tool == ActiveTool.DIMENSION:
            self._dimension_click(side, x, y)
            return
        if tool == ActiveTool.CONTOUR:
            self._contour_click(side, x, y)
            return
        if tool == ActiveTool.CROP:
            self._crop_press(side, x, y)
            return
        if self.controller.mesh_edit:
            self._mesh_click(side, x, y)
            return
        hits = self._hit_objects(side, x, y)
        mods = QApplication.keyboardModifiers()
        if hits and tool in (ActiveTool.PAN, ActiveTool.SELECT):
            if mods & Qt.KeyboardModifier.AltModifier and self._hit_cycle:
                self._hit_cycle_i = (self._hit_cycle_i + 1) % len(self._hit_cycle)
                choice = self._hit_cycle[self._hit_cycle_i]
            else:
                self._hit_cycle = hits
                self._hit_cycle_i = 0
                choice = hits[0]
            add = bool(mods & Qt.KeyboardModifier.ControlModifier)
            self._set_selection([choice], add=add)
            if choice[0] == "landmark":
                self._show_mesh_item(choice[1])
                if not add:
                    lm = next((x for x in self.controller.data.landmarks if x.id == choice[1]), None)
                    old = lm.coord(side) if lm else None
                    self._moving = (choice[1], side, old)
            return
        if tool == ActiveTool.SELECT:
            if not (mods & Qt.KeyboardModifier.ControlModifier):
                self._set_selection([])
            return
        if tool in (
            ActiveTool.DISTANCE,
            ActiveTool.ANGLE,
            ActiveTool.CIRCLE,
            ActiveTool.PITCH,
            ActiveTool.CROSS_SECTION,
        ):
            self.tool_clicks.append((side, x, y))
            self._finish_tool_if_ready(tool)
            return
        self.controller.place_point(side, x, y)
        self.refresh_overlays()
        if tool == ActiveTool.CALIBRATION:
            return

    def _finish_tool_if_ready(self, tool: ActiveTool) -> None:
        pts = self.tool_clicks
        if tool == ActiveTool.DISTANCE and len(pts) >= 2:
            (_, x0, y0), (_, x1, y1) = pts[-2], pts[-1]
            dx, dy = x1 - x0, y1 - y0
            dist = math.hypot(dx, dy)
            ang = math.degrees(math.atan2(dy, dx))
            QMessageBox.information(
                self,
                "Distance",
                f"Distance: {dist:.4f} px\nDX: {dx:.4f}\nDY: {dy:.4f}\nAngle: {ang:.3f}°\n"
                + self._phys_line(pts[-1][0], dist, dx, dy),
            )
            self.tool_clicks.clear()
        elif tool == ActiveTool.ANGLE and len(pts) >= 3:
            from core.geometry import Point2D, three_point_angle_deg

            a, v, b = pts[-3], pts[-2], pts[-1]
            ang = three_point_angle_deg(Point2D(a[1], a[2]), Point2D(v[1], v[2]), Point2D(b[1], b[2]))
            QMessageBox.information(self, "Angle", f"{ang:.4f} degrees")
            self.tool_clicks.clear()
        elif tool == ActiveTool.CIRCLE and len(pts) >= 3:
            arr = np.array([[p[1], p[2]] for p in pts], dtype=np.float64)
            try:
                res = fit_circle(arr)
            except ValueError as exc:
                QMessageBox.warning(self, "Circle", str(exc))
                return
            QMessageBox.information(
                self,
                "Circle fit",
                f"Center: {res['center']}\nRadius: {res['radius']:.4f}\n"
                f"Diameter: {res['diameter']:.4f}\nFit RMSE: {res['fit_residual_rmse']:.4f}\n"
                "Circle fitted from the last 3+ clicks.",
            )
            self.tool_clicks.clear()
        elif tool == ActiveTool.PITCH:
            self.statusBar().showMessage(f"Pitch: {len(pts)} ordered points — press Enter to compute")
        elif tool == ActiveTool.CROSS_SECTION and len(pts) >= 1:
            self._one_section(pts[-1])
            self.tool_clicks.clear()

    def _phys_line(self, side: Side, dist: float, dx: float, dy: float) -> str:
        img = self.controller.image(side)
        if img is None or not img.calibration.is_calibrated:
            other = self.controller.image(Side.B if side == Side.A else Side.A)
            if other is None or not other.calibration.is_calibrated:
                u = self.controller.data.display_unit.value
                return f"— {u}   PHYSICAL SCALE NOT SET"
            img = other
        s = img.calibration.mm_per_px
        u = self.controller.data.display_unit.value
        from models.units import px_to_unit

        return (
            f"Physical: {px_to_unit(dist, s, self.controller.data.display_unit):.4f} {u}  "
            f"DX={px_to_unit(dx, s, self.controller.data.display_unit):.4f}  "
            f"DY={px_to_unit(dy, s, self.controller.data.display_unit):.4f}"
        )

    def _cursor(self, side: Side, x: float, y: float) -> None:
        self._last_cursor[side] = (x, y)
        img = self.controller.image(side)
        orig = (x, y)
        if img:
            orig = img.from_oriented(x, y)
        px_lab = self.st_a_px if side == Side.A else self.st_b_px
        ph_lab = self.st_a_ph if side == Side.A else self.st_b_ph
        if self.controller.data.show_pixel_values:
            px_lab.setText(f"{side.value} px: {orig[0]:.2f}, {orig[1]:.2f}")
            px_lab.show()
        else:
            px_lab.hide()
        from models.units import format_physical_or_pending, px_to_unit

        u = self.controller.data.display_unit
        calibrated = bool(img and img.calibration.is_calibrated)
        if calibrated:
            zx, zy = self._display_zero_offset(side)
            phys = (
                f"{side.value}: {px_to_unit(orig[0] - zx, img.calibration.mm_per_px, u):.4f}, "
                f"{px_to_unit(orig[1] - zy, img.calibration.mm_per_px, u):.4f} {u.value}"
            )
        else:
            phys = format_physical_or_pending(None, u, calibrated=False)
        ph_lab.setText(phys)
        if self.controller.sync_cursor:
            self._sync_cursor(side, x, y)
        else:
            self.view_a.set_peer_cursor(None)
            self.view_b.set_peer_cursor(None)
        za = self.view_a.quality_diag()
        zb = self.view_b.quality_diag()
        src_txt = "SOURCE 1:1" if za.get("source_1to1") else f"{za['zoom']*100:.0f}%"
        self.st_zoom.setText(
            f"{src_txt}  Original: {za['source'][0]}×{za['source'][1]}  "
            f"Rendering from original: {'YES' if za.get('rendering_from_original') else 'NO'}  "
            f"DPR {za.get('dpr', 1):.2f}  {za.get('interpolation', '')}"
        )
        wx, wy = self.controller.unview_xy(side, x, y)
        hits = self._hit_objects(side, wx, wy)
        new_hover = hits[0] if hits else None
        if new_hover != self._hover and not self._moving:
            self._hover = new_hover
            self.refresh_overlays()

    def _display_zero_offset(self, side: Side) -> tuple[float, float]:
        cs = self.controller.data.board_cs
        if cs.display_zero_x is None or cs.display_zero_side != side.value:
            return 0.0, 0.0
        return cs.display_zero_x, cs.display_zero_y

    def _sync_from(self, src: ImageView, dst: ImageView) -> None:
        if self._syncing or getattr(self, "_overlay_workspace", False):
            return
        self._syncing = True
        try:
            if self.controller.sync_zoom:
                src_z = src.current_zoom()
                dst_z = dst.current_zoom()
                if dst_z > 0 and abs(src_z - dst_z) > 1e-6:
                    dst.scale(src_z / dst_z, src_z / dst_z)
            if self.controller.sync_pan:
                cx, cy = src.mapToScene(src.viewport().rect().center()).x(), src.mapToScene(src.viewport().rect().center()).y()
                wx, wy = self.controller.unview_xy(src.side, cx, cy)
                xf = self.controller.accepted_transform()
                if xf is not None:
                    if src.side == Side.A:
                        wx, wy = xf.apply_one(wx, wy)
                    else:
                        wx, wy = xf.inverse().apply_one(wx, wy)
                    vx, vy = self.controller.view_xy(dst.side, wx, wy)
                    dst.centerOn(vx, vy)
                else:
                    dst.centerOn(cx, cy)
            dst._apply_interpolation()
        finally:
            self._syncing = False

    def _sync_cursor(self, side: Side, x: float, y: float) -> None:
        xf = self.controller.accepted_transform()
        other = self.view_b if side == Side.A else self.view_a
        other_side = Side.B if side == Side.A else Side.A
        if xf is None:
            other.set_peer_cursor((x, y))
            return
        wx, wy = self.controller.unview_xy(side, x, y)
        if side == Side.A:
            px, py = xf.apply_one(wx, wy)
        else:
            px, py = xf.inverse().apply_one(wx, wy)
        vx, vy = self.controller.view_xy(other_side, px, py)
        other.set_peer_cursor((vx, vy))

    # ----- refresh -----
    def _display_array_for_view(self, side: Side):
        doc = self.controller.image(side)
        enhanced = self.controller.view_array(side)
        original = self.controller.original_view_array(side)
        if doc is None or enhanced is None:
            return enhanced
        cmp = doc.adjustments.compare_mode
        if cmp == "original" or doc.adjustments.enhance_mode == "OFF":
            return original
        if cmp == "split" and original is not None and enhanced is not None:
            import numpy as np

            mid = original.shape[1] // 2
            out = np.ascontiguousarray(enhanced.copy())
            out[:, :mid] = original[:, :mid]
            return out
        if cmp == "flicker":
            return original if getattr(self, "_enhance_flicker_orig", False) else enhanced
        return enhanced

    def refresh_images(self) -> None:
        mode = self.controller.data.overlay_mode
        show_a, show_b = self._display_array_for_view(Side.A), self._display_array_for_view(Side.B)
        if mode in (OverlayMode.DIFFERENCE, OverlayMode.EDGE_DIFFERENCE) and show_a is not None and show_b is not None:
            warped = self._warp_b_onto_a()
            if warped is not None:
                show_a = difference_image(show_a, warped) if mode == OverlayMode.DIFFERENCE else edge_difference(show_a, warped)
        reset_a = getattr(self, "_last_a_shape", None) != (None if show_a is None else show_a.shape)
        reset_b = getattr(self, "_last_b_shape", None) != (None if show_b is None else show_b.shape)
        self._last_a_shape = None if show_a is None else show_a.shape
        self._last_b_shape = None if show_b is None else show_b.shape
        self.view_a.set_image_array(show_a, reset_view=reset_a)
        self.view_b.set_image_array(show_b, reset_view=reset_b)
        self.view_a.set_original_array(self.controller.original_view_array(Side.A))
        self.view_b.set_original_array(self.controller.original_view_array(Side.B))
        self._apply_appearance()
        for side, view in ((Side.A, self.view_a), (Side.B, self.view_b)):
            img = self.controller.image(side)
            view.set_working_region(None if img is None else self.controller.crop_view_rect(side), fit=False)
            adj = None if img is None else img.adjustments
            if adj is not None and adj.display_is_enhanced() and adj.compare_mode != "original":
                view._title.setText(f"MODEL {side.value}  —  {self._enhance_caption(adj)}")
            else:
                view._title.setText(f"MODEL {side.value}")
        if self._overlay_workspace or overlay_is_registered(mode):
            self._apply_overlay_layers(show_a, show_b)
        else:
            self.view_a.clear_registered_layer()
            self.view_b.clear_registered_layer()
            self.view_a.set_base_opacity(1.0)
            self.view_b.set_base_opacity(1.0)
        enhance_flicker = any(
            img is not None and img.adjustments.compare_mode == "flicker"
            for img in (self.controller.image(Side.A), self.controller.image(Side.B))
        )
        if (mode == OverlayMode.FLICKER or enhance_flicker) and not self._flicker_timer.isActive() and self.controller.data.flicker_hz > 0:
            self._flicker_timer.start(int(1000 / max(self.controller.data.flicker_hz, 0.2)))
        elif mode != OverlayMode.FLICKER and not enhance_flicker:
            self._flicker_timer.stop()
        self._update_pane_captions()
        self._draw_vectors()
        self.refresh_overlays()

    def _apply_overlay_layers(self, show_a, show_b) -> None:
        xf = self.controller.accepted_transform()
        mode = self.controller.data.overlay_mode
        op = self.controller.data.overlay_opacity
        if xf is None:
            self.view_a.clear_registered_layer()
            self.view_b.clear_registered_layer()
            return
        base = overlay_base_side(mode)
        if self._space_down:
            op = 1.0
        if base == Side.A:
            # B OVER A — A is base, B transformed into A
            self.view_b.clear_registered_layer()
            if show_b is None:
                self.view_a.clear_registered_layer()
                return
            self.view_a.set_registered_layer(show_b, _overlay_qtransform(self.controller, base=Side.A), op)
            self.view_a.set_base_opacity(1.0)
            self.view_a.set_layer_opacity(op)
        else:
            # A OVER B — B is base, A transformed into B
            self.view_a.clear_registered_layer()
            if show_a is None:
                self.view_b.clear_registered_layer()
                return
            self.view_b.set_registered_layer(show_a, _overlay_qtransform(self.controller, base=Side.B), op)
            self.view_b.set_base_opacity(1.0)
            self.view_b.set_layer_opacity(op)

    def _auto_flicker(self) -> None:
        self._flicker_toggle = not self._flicker_toggle
        self._enhance_flicker_orig = not getattr(self, "_enhance_flicker_orig", False)
        if self._overlay_workspace:
            view = self.view_b if overlay_base_side(self.controller.data.overlay_mode) == Side.B else self.view_a
            if self._flicker_toggle:
                view.set_base_opacity(0.0)
                view.set_layer_opacity(1.0)
            else:
                view.set_base_opacity(1.0)
                view.set_layer_opacity(0.0)
            return
        self.refresh_images()

    def _warp_b_onto_a(self):
        import cv2

        xf = self.controller.accepted_transform()
        a = self.controller.display_array(Side.A)
        b = self.controller.display_array(Side.B)
        if xf is None or a is None or b is None:
            return None
        ha, wa = a.shape[:2]
        m = xf.matrix()[:2].astype(np.float32)
        return cv2.warpAffine(b, m, (wa, ha), flags=cv2.INTER_LINEAR)

    def _draw_vectors(self) -> None:
        for side, view in ((Side.A, self.view_a), (Side.B, self.view_b)):
            img = self.controller.image(side)
            if img and img.vector:
                # If the image is calibrated, vector mm → px
                px_per = 1.0
                if img.calibration.is_calibrated and img.vector.unit == "mm":
                    px_per = 1.0 / img.calibration.mm_per_px
                view.add_vector_primitives(img.vector.visible_primitives(), px_per)

    def refresh_overlays(self) -> None:
        self.view_a.clear_overlays()
        self.view_b.clear_overlays()
        self.view_a.set_marker_colors(self.controller.data.marker_colors)
        self.view_b.set_marker_colors(self.controller.data.marker_colors)
        from core.analysis import control_match_state

        xf = self.controller.accepted_transform()
        mesh = self.controller.data.mesh
        sel_ids = {s[1] for s in self._selection if s[0] == "landmark"}
        primary = self._selection[0] if self._selection else None
        hover_id = self._hover[1] if self._hover and self._hover[0] == "landmark" else None
        dense = sum(1 for lm in self.controller.data.landmarks if lm.visible) > 8
        show_all_ids = bool(mesh.show_ids) and not dense
        placed: list[tuple[float, float, Side]] = []
        for lm in self.controller.data.landmarks:
            if not getattr(lm, "visible", True):
                continue
            if not mesh.show_points and lm.point_type == PointType.CONTROL:
                continue
            if not self.controller.data.show_measurements and lm.point_type == PointType.MEASUREMENT:
                continue
            if not self.controller.data.show_zero_scale and lm.point_type in (PointType.ZERO, PointType.SCALE):
                continue
            state = control_match_state(self.controller, lm) if lm.point_type in (PointType.CONTROL, PointType.MEASUREMENT) else ""
            mismatch = state == "MISMATCH"
            selected = lm.id in sel_ids
            hovered = lm.id == hover_id
            is_primary = bool(primary and primary[0] == "landmark" and primary[1] == lm.id)
            show_lab = selected or hovered or show_all_ids or (bool(mesh.show_ids) and selected)
            if lm.has_a:
                ax, ay = self.controller.view_xy(Side.A, *self.controller._work_xy(Side.A, lm.a_x, lm.a_y))
                off = self._label_offset(placed, ax, ay, Side.A)
                self.view_a.add_point_marker(
                    ax, ay, lm.label or lm.id, lm.point_type, state=state, selected=selected, hovered=hovered,
                    show_label=show_lab, label_offset=off, payload=f"landmark:{lm.id}",
                    primary=is_primary, pulse=self._selection_pulse and is_primary,
                )
            if lm.has_b and mesh.show_actual:
                bx, by = self.controller.view_xy(Side.B, *self.controller._work_xy(Side.B, lm.b_x, lm.b_y))
                off = self._label_offset(placed, bx, by, Side.B)
                self.view_b.add_point_marker(
                    bx, by, lm.label or lm.id, lm.point_type, state=state, selected=selected, hovered=hovered,
                    show_label=show_lab, label_offset=off, payload=f"landmark:{lm.id}",
                    primary=is_primary, pulse=self._selection_pulse and is_primary,
                )
            if xf and lm.point_type in (PointType.MEASUREMENT, PointType.CONTROL) and mesh.show_expected:
                if lm.has_a and not lm.has_b:
                    pred = self.controller.predict_b_work(lm.a_x, lm.a_y)
                    if pred:
                        vx, vy = self.controller.view_xy(Side.B, *pred)
                        self.view_b.add_point_marker(
                            vx, vy, f"{lm.label} expected", lm.point_type, predicted=True, state="EXPECTED_ONLY",
                            selected=selected, primary=is_primary, pulse=self._selection_pulse and is_primary,
                            show_label=selected,
                        )
                if lm.has_b and not lm.has_a:
                    pred = self.controller.predict_a_work(lm.b_x, lm.b_y)
                    if pred:
                        vx, vy = self.controller.view_xy(Side.A, *pred)
                        self.view_a.add_point_marker(
                            vx, vy, f"{lm.label} expected", lm.point_type, predicted=True, state="EXPECTED_ONLY",
                            selected=selected, primary=is_primary, pulse=self._selection_pulse and is_primary,
                            show_label=selected,
                        )
                if lm.has_a and lm.has_b:
                    pred = self.controller.predict_b_work(lm.a_x, lm.a_y)
                    if pred and self.controller.data.mesh.show_vectors:
                        bx, by = self.controller.view_xy(Side.B, *self.controller._work_xy(Side.B, lm.b_x, lm.b_y))
                        px, py = self.controller.view_xy(Side.B, *pred)
                        self.view_b.add_residual(px, py, bx, by, lm.id, mismatch=mismatch)
                    if pred and self.controller.data.tolerances.show_circles:
                        from core.analysis import effective_tolerance_px

                        try:
                            radius, _, _ = effective_tolerance_px(self.controller, lm.group)
                        except ValueError:
                            radius = None
                        if radius:
                            px, py = self.controller.view_xy(Side.B, *pred)
                            self.view_b.add_tolerance_circle(px, py, radius, match=not mismatch)
        self._draw_mesh()
        rec = self.controller.data.current_transform
        if rec:
            for r in rec.residuals:
                self.view_b.add_residual(r["predicted_x"], r["predicted_y"], r["actual_x"], r["actual_y"], r["point_id"])
        from core.contour_geom import sample_contour

        if self.controller.data.show_contours:
          for c in self.controller.data.contours:
            if not c.visible:
                continue
            key = "contour_a" if c.side == Side.A else "contour_b"
            view = self.view_a if c.side == Side.A else self.view_b
            sampled = sample_contour(c)
            pts = []
            for p in sampled:
                wx, wy = self.controller._work_xy(c.side, float(p[0]), float(p[1]))
                pts.append(self.controller.view_xy(c.side, wx, wy))
            mismatch = None
            if c.role == "ACTUAL" and c.pair_id:
                exp = next((x for x in self.controller.data.contours if x.id == c.pair_id or x.pair_id == c.id and x.role == "EXPECTED"), None)
                if exp is not None:
                    from core.contour_geom import contour_deviation

                    ev = sample_contour(exp)
                    evw = np.array([self.controller._work_xy(exp.side, float(p[0]), float(p[1])) for p in ev], dtype=np.float64)
                    avw = np.array([self.controller._work_xy(c.side, float(p[0]), float(p[1])) for p in sampled], dtype=np.float64)
                    if xf and exp.side == Side.A:
                        evw = xf.apply(evw)
                    stats = contour_deviation(evw, avw)
                    img = self.controller.image(c.side)
                    scale = img.calibration.mm_per_px if img and img.calibration.is_calibrated else None
                    tol = self.controller.data.tolerances.match
                    if self.controller.data.tolerances.unit.value != "px" and scale:
                        tol_px = tol / scale
                    else:
                        tol_px = tol if self.controller.data.tolerances.unit.value == "px" else 0
                    if stats.get("samples"):
                        mismatch = [d > tol_px for d in stats["samples"]]
            csel = any(s[0] == "contour" and s[1] == c.id for s in self._selection)
            node_sel = [s for s in self._selection if s[0] == "contour_node" and s[1] == c.id]
            view.add_contour_path(pts, key, closed=c.closed, mismatch_mask=mismatch, selected=csel or bool(node_sel))
            if self.controller.active_tool == ActiveTool.CONTOUR or csel or node_sel:
                for i, v in enumerate(c.vertices):
                    wx, wy = self.controller.view_xy(c.side, *self.controller._work_xy(c.side, v.x, v.y))
                    selected = bool(getattr(self, "_contour_edit", None) and self._contour_edit[0] == c.id)
                    selected = selected or any(s[2] == i for s in node_sel)
                    view.add_handle(wx, wy, kind="node", selected=selected)
                    if v.c1x is not None and v.c1y is not None:
                        hx, hy = self.controller.view_xy(c.side, *self.controller._work_xy(c.side, v.c1x, v.c1y))
                        view.add_handle(hx, hy, kind="handle")
                    if v.c2x is not None and v.c2y is not None:
                        hx, hy = self.controller.view_xy(c.side, *self.controller._work_xy(c.side, v.c2x, v.c2y))
                        view.add_handle(hx, hy, kind="handle")
        self._draw_dimensions()
        ca = next((c for c in self.controller.data.contours if c.side == Side.A), None)
        cb = next((c for c in self.controller.data.contours if c.side == Side.B), None)
        if ca and cb and xf and len(ca.vertices) >= 2 and len(cb.vertices) >= 2:
            try:
                from core.contours import contour_point_residuals

                include = {"CONFIRMED", "CONFIRMED_VISIBLE"}
                if self.controller.data.include_partial_in_stats:
                    include |= {"PARTIAL", "PARTIALLY_VISIBLE"}
                if self.controller.data.include_cropped_in_stats:
                    include |= {"CROPPED"}
                work_a = np.array(
                    [self.controller._work_xy(Side.A, v.x, v.y) for v in ca.vertices], dtype=np.float64
                )
                work_b = np.array(
                    [self.controller._work_xy(Side.B, v.x, v.y) for v in cb.vertices], dtype=np.float64
                )
                predicted = xf.apply(work_a)
                cres = contour_point_residuals(
                    predicted,
                    work_b,
                    closed_a=ca.closed,
                    closed_b=cb.closed,
                    confidence_a=ca.confidence_list(),
                    include_confidence=include,
                )
                step = max(1, self.contour_panel.vector_density.value())
                for r in cres[::step]:
                    self.view_b.add_residual(r.ax, r.ay, r.bx, r.by, f"ctr{r.index}")
            except Exception:
                pass
        self.point_table.refresh(self._selection)
        self.meas_panel.refresh()
        self.group_panel.refresh()
        self.contour_panel.refresh()
        self.view_a.lock_scene_to_image()
        self.view_b.lock_scene_to_image()

    def refresh_chrome(self) -> None:
        rec = self.controller.data.current_transform
        locked = rec is not None and rec.status == TransformStatus.LOCKED
        extra = ""
        if rec is None:
            self.st_reg.setText("Alignment: none")
            self.lock_label.setText("")
            self.st_scale.setText("Scale —")
            self.st_rot.setText("Rotation —")
        else:
            self.st_reg.setText(f"Alignment: {rec.status.value}")
            t = rec.transform
            self.st_scale.setText(f"Scale {t.scale:.4f}×")
            self.st_rot.setText(f"Rotation {t.rotation_deg:.3f}°")
            extra = f"Scale {t.scale:.3f}×  Rotation {t.rotation_deg:.2f}°"
            if rec.status == TransformStatus.LOCKED:
                self.lock_label.setText("Alignment LOCKED")
                self.lock_label.setStyleSheet("background:#922b21; color:#fff; font-weight:700; padding:4px 10px;")
            else:
                self.lock_label.setText(rec.status.value)
                self.lock_label.setStyleSheet("padding:4px 10px;")
        if self.controller.analysis_is_stale():
            from app.i18n import tr

            extra = (extra + "  — " + tr("sel.results_stale")).strip(" —")
        self.workflow.set_step(self.controller.workflow_step(), locked=locked, extra=extra)
        a = self.controller.data.image_a
        b = self.controller.data.image_b
        sa = a.pcb_side.value if a else "?"
        sb = b.pcb_side.value if b else "?"
        warn = ""
        if a and b and reflection_required_for_opposite_sides(sa, sb):
            warn = "  — opposite sides: reflection is required and must be accepted explicitly."
        self.convention.setText(
            f"{sa} LEFT / {sa} RIGHT    |    {sb} LEFT / {sb} RIGHT    "
            f"axis X {self.controller.data.board_cs.axis_x.value}  "
            f"Y {self.controller.data.board_cs.axis_y.value}{warn}"
        )
        self.inspector.refresh()
        self.summary.refresh()
        title = "PCB Image Comparator"
        if self.controller.path:
            title += f" — {self.controller.path.name}"
        if self.controller.dirty:
            title += " *"
        self.setWindowTitle(title)
        self._sync_toolbar_tolerance()
        self._sync_scale_status()
        self._refresh_mode_ui()

    def refresh_all(self) -> None:
        self.refresh_images()
        self.refresh_chrome()

    # ----- file -----
    def _load_image(self, side: Side) -> None:
        settings = qt_settings()
        start = settings.value("last_image_dir", "")
        path, _ = QFileDialog.getOpenFileName(self, f"Open Image {side.value}", start, IMAGE_DIALOG_FILTER)
        if path:
            settings.setValue("last_image_dir", str(Path(path).parent))
            if self.controller.image(side) is not None:
                if QMessageBox.question(
                    self,
                    "Replace image",
                    f"Replace MODEL {side.value}? Associated points stay unless you remove them separately.",
                ) != QMessageBox.StandardButton.Yes:
                    return
            self.controller.load_image(side, path)
            self._last_a_shape = self._last_b_shape = None
            self.refresh_images()

    def _new(self) -> None:
        if self.controller.dirty and QMessageBox.question(self, "New", "Discard unsaved changes?") != QMessageBox.StandardButton.Yes:
            return
        self.controller.new_project()
        self.refresh_all()

    def _open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open project", "", "PCB Comparator (*.picmp)")
        if path:
            self.controller.load(path)
            self._remember_project(Path(path))
            self.refresh_all()

    def _save(self) -> None:
        if self.controller.path is None:
            self._save_as()
            return
        self.controller.save()
        self._remember_project(self.controller.path)

    def _save_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save project", "project.picmp", "PCB Comparator (*.picmp)")
        if path:
            self.controller.save(path)
            self._remember_project(Path(path))

    def _import_svg(self) -> None:
        path, side = self._pick_import("SVG (*.svg)")
        if path:
            self.controller.set_vector(side, load_svg(path, side))
            self.refresh_images()

    def _import_dxf(self) -> None:
        path, side = self._pick_import("DXF (*.dxf)")
        if path:
            self.controller.set_vector(side, load_dxf(path, side))
            self.refresh_images()

    def _import_easyeda(self) -> None:
        path, side = self._pick_import("EasyEDA JSON (*.json)")
        if path:
            self.controller.set_vector(side, load_easyeda(path, side))
            self.refresh_images()

    def _import_csv_points(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "CSV points", "", "CSV (*.csv)")
        if not path:
            return
        for lm in load_points_csv(path):
            self.controller.data.landmarks.append(lm)
        self.controller.pointsChanged.emit()
        self.controller.mark_dirty()

    def _import_csv_contour(self) -> None:
        path, side = self._pick_import("CSV (*.csv)")
        if path:
            c = load_contour_csv(path, side, self.controller.data.next_id("CTR"))
            self.controller.data.contours.append(c)
            self.controller.contoursChanged.emit()
            self.controller.mark_dirty()

    def _pick_import(self, filt: str):
        path, _ = QFileDialog.getOpenFileName(self, "Import", "", filt)
        if not path:
            return None, None
        side, ok = QInputDialog.getItem(self, "Import side", "Load onto:", ["B", "A"], 0, False)
        if not ok:
            return None, None
        return path, Side.A if side == "A" else Side.B

    def _export_points(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", "points.csv", "CSV (*.csv)")
        if path:
            export_points(path, self.controller.data, self.controller.data.display_unit)

    def _export_simple(self, fn) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", "export.csv", "CSV (*.csv)")
        if path:
            try:
                fn(path, self.controller.data)
            except ValueError as exc:
                QMessageBox.warning(self, "Export", str(exc))

    def _export_view(self, kind: str) -> None:
        path, filt = QFileDialog.getSaveFileName(
            self, "Export image", f"{kind}.png", "PNG (*.png);;JPEG (*.jpg *.jpeg)"
        )
        if not path:
            return
        a = self.controller.display_array(Side.A)
        warped = self._warp_b_onto_a()
        if a is None:
            QMessageBox.warning(self, "Export", "Image A is not loaded.")
            return
        if kind == "overlay":
            rgb = overlay_images(a, warped if warped is not None else a, self.controller.data.overlay_opacity)
        elif kind == "difference":
            if warped is None:
                QMessageBox.warning(self, "Export", "Accept a transform before difference export.")
                return
            rgb = difference_image(a, warped)
        else:
            if warped is None:
                QMessageBox.warning(self, "Export", "Accept a transform before edge-difference export.")
                return
            rgb = edge_difference(a, warped)
        quality = self._jpeg_quality_if_needed(path)
        out = rgb if rgb.ndim == 3 and rgb.shape[2] == 3 else array_to_rgb(rgb)
        save_rgb(path, out, jpeg_quality=quality)
        h, w = out.shape[:2]
        self.statusBar().showMessage(f"Exported {kind} at original resolution {w}×{h}")

    def _pdf(self) -> None:
        ReportDialog(self.controller, self).exec()

    # ----- registration actions -----
    def _review_transform(self) -> None:
        TransformDialog(self.controller, self).exec()
        self.refresh_all()

    def _orientations(self) -> None:
        OrientationDialog(self.controller, self).exec()
        self.refresh_all()

    def _quick_fit(self, mode: TransformMode) -> None:
        self.controller.compute_provisional(mode)
        TransformDialog(self.controller, self).exec()
        self.refresh_all()

    def _lock(self) -> None:
        self.controller.lock_alignment()
        self.refresh_chrome()

    def _unlock(self) -> None:
        if QMessageBox.question(
            self,
            "Unlock alignment",
            "Unlocking allows the accepted transform to be replaced. Continue?",
        ) == QMessageBox.StandardButton.Yes:
            self.controller.unlock_alignment()
            self.refresh_chrome()

    def _board_cs(self) -> None:
        from ui.board_cs_dialog import BoardCoordinateDialog

        BoardCoordinateDialog(self.controller, self).exec()
        self.refresh_chrome()

    def _accept_proposal(self, accept: bool) -> None:
        item = self.contour_panel.list.currentItem()
        if item is None:
            QMessageBox.warning(self, "Proposal", "Select a contour in the Contours panel.")
            return
        cid = item.text().split()[0]
        self.controller.accept_edge_proposal(cid, accept)
        self.refresh_overlays()

    def _calibrate(self) -> None:
        self.set_tool(ActiveTool.CALIBRATION)
        CalibrationDialog(self.controller, self).exec()
        self.refresh_chrome()

    def _lock_needed(self) -> bool:
        rec = self.controller.data.current_transform
        return rec is not None and rec.status == TransformStatus.LOCKED

    def _compare_outlines(self) -> None:
        contours = self.controller.data.contours
        a = next((c for c in contours if c.side == Side.A), None)
        b = next((c for c in contours if c.side == Side.B), None)
        if a is None or b is None:
            QMessageBox.warning(self, "Board outlines", "Trace or import a contour on A and on B first.")
            return
        include = {Confidence for Confidence in ["CONFIRMED", "CONFIRMED_VISIBLE"]}
        if self.controller.data.include_partial_in_stats:
            include |= {"PARTIAL", "PARTIALLY_VISIBLE"}
        if self.controller.data.include_cropped_in_stats:
            include |= {"CROPPED"}
        mode, ok = QInputDialog.getItem(self, "Outline fit", "Mode:", ["RIGID", "SIMILARITY"], 0, False)
        if not ok:
            return
        tm = TransformMode.RIGID if mode == "RIGID" else TransformMode.SIMILARITY
        try:
            result = register_contours(a.points_array(), b.points_array(), mode=tm, closed=a.closed and b.closed)
        except Exception as exc:
            QMessageBox.warning(self, "Board outlines", str(exc))
            return
        residuals = contour_point_residuals(
            a.points_array(),
            result.transform.apply(a.points_array()) if False else b.points_array(),
            closed_a=a.closed,
            closed_b=b.closed,
            confidence_a=a.confidence_list(),
            include_confidence=include,
        )
        # After fit, compare transformed A to B
        transformed = result.transform.apply(a.points_array())
        residuals = contour_point_residuals(
            transformed,
            b.points_array(),
            closed_a=a.closed,
            closed_b=b.closed,
            confidence_a=a.confidence_list(),
            include_confidence=include,
        )
        report = contour_residual_report(residuals, tuple(self.controller.data.tolerances.contour_thresholds))
        if QMessageBox.question(
            self,
            "Compare board outlines",
            f"Preview (not accepted yet)\nscale={result.transform.scale:.6f} rot={result.transform.rotation_deg:.4f}°\n"
            f"RMSE={report.get('rmse')} median={report.get('median')} max={report.get('max')}\n"
            f"signed mean={report.get('signed_mean')}\nACCEPT this contour registration?",
        ) == QMessageBox.StandardButton.Yes:
            from models.transform import TransformRecord
            from models.enums import TransformStatus

            rec = TransformRecord(
                transform=result.transform,
                status=TransformStatus.ACCEPTED,
                mode=tm,
                residuals=[r.as_dict() for r in result.residuals],
                summary=result.summary.as_dict(),
                notes=["Contour registration — user accepted"],
                label="Board outline",
            )
            self.controller.data.current_transform = rec
            self.controller.data.transform_history.append(rec)
            self.controller.lock_alignment()
            self.refresh_all()

    def _compare_group(self) -> None:
        names = [g.name for g in self.controller.data.groups]
        name, ok = QInputDialog.getItem(self, "Compare contact group", "Group:", names, 0, False)
        if not ok:
            return
        comps = [c for c in self.controller.group_comparisons() if c.name == name]
        if not comps:
            QMessageBox.warning(self, "Group", "No points in that group.")
            return
        c = comps[0]
        QMessageBox.information(
            self,
            "Contact group",
            f"INTERNAL GEOMETRY: {c.internal_state.value}\n"
            f"Mean pitch A={None if not c.geometry_a else c.geometry_a.mean_pitch}  "
            f"B={None if not c.geometry_b else c.geometry_b.mean_pitch}\n"
            f"BOARD / GLOBAL POSITION DX={c.board_dx} DY={c.board_dy}\n"
            "The group was not moved independently.",
        )

    def _one_section(self, click) -> None:
        side, x, y = click
        a = next((c for c in self.controller.data.contours if c.side == Side.A), None)
        b = next((c for c in self.controller.data.contours if c.side == Side.B), None)
        if a is None or b is None:
            QMessageBox.warning(self, "Cross-section", "Need a contour on A and B.")
            return
        axis = CrossSectionAxis.HORIZONTAL
        pos = y
        try:
            sec = width_cross_section(a.points_array(), b.points_array(), pos, axis, closed_a=a.closed, closed_b=b.closed)
        except ValueError as exc:
            QMessageBox.warning(self, "Cross-section", str(exc))
            return
        QMessageBox.information(self, "Cross-section", str(sec.as_dict()))

    def _auto_sections(self) -> None:
        a = next((c for c in self.controller.data.contours if c.side == Side.A), None)
        b = next((c for c in self.controller.data.contours if c.side == Side.B), None)
        if a is None or b is None:
            QMessageBox.warning(self, "Cross-sections", "Need contours on A and B.")
            return
        step, ok = QInputDialog.getDouble(self, "Step", "Every (image units):", 1.0, 0.01, 1e6, 3)
        if not ok:
            return
        secs = automatic_cross_sections(a.points_array(), b.points_array(), step=step, closed_a=a.closed, closed_b=b.closed)
        self.width_plot.plot(secs)
        path, _ = QFileDialog.getSaveFileName(self, "Export width CSV", "width.csv", "CSV (*.csv)")
        if path:
            export_width_sections(path, secs, self.controller.data.display_unit)

    def _show_diagnostics(self) -> None:
        findings = self.controller.run_diagnostics()
        if not findings:
            QMessageBox.information(self, "Diagnostics", "No diagnostic suspicions.")
            return
        QMessageBox.information(self, "Diagnostics", "\n\n".join(f.message for f in findings))

    def _set_pcb_side(self, side: Side, pcb: PcbSide) -> None:
        img = self.controller.image(side)
        if img:
            img.pcb_side = pcb
            self.controller.mark_dirty()
            self.refresh_chrome()

    def _pcb_workspace(self) -> None:
        from models.point_group import default_pcb_groups

        self.controller.data.workspace = "PCB_COMPARISON"
        have = {g.name for g in self.controller.data.groups}
        for g in default_pcb_groups():
            if g.name not in have:
                self.controller.data.groups.append(g)
        self.group_panel.refresh()

    def _perspective(self) -> None:
        PerspectiveDialog(self.controller, self).exec()
        self.refresh_all()

    def _tolerances(self) -> None:
        self._set_tolerance()
        name, ok = QInputDialog.getText(self, "Tolerance profile", "Save user profile name (blank to skip):")
        if ok and name.strip():
            from app.settings import load_tolerance_profiles, save_tolerance_profiles

            self.controller.data.tolerances.name = name.strip()
            profiles = load_tolerance_profiles()
            profiles = [p for p in profiles if p.get("name") != name.strip()]
            profiles.append(self.controller.data.tolerances.as_dict())
            save_tolerance_profiles(profiles)

    def _units(self) -> None:
        names = [u.value for u in Unit]
        cur, ok = QInputDialog.getItem(self, "Display units", "Unit:", names, names.index(self.controller.data.display_unit.value), False)
        if ok:
            self.controller.data.display_unit = Unit(cur)
            self.refresh_overlays()

    def _colors(self) -> None:
        key, ok = QInputDialog.getItem(self, "Marker color", "Which:", list(self.controller.data.marker_colors), 0, False)
        if not ok:
            return
        val, ok = QInputDialog.getText(self, "Hex color", "Color:", text=self.controller.data.marker_colors[key])
        if ok and val:
            self.controller.data.marker_colors[key] = val
            self.refresh_overlays()

    def _manager_double_click(self, item) -> None:
        if isinstance(item, str):
            item = ("landmark", item, None)
        self._set_selection([item], pulse=False)
        self._center_item(item, sides=(Side.A, Side.B))

    def _item_scene_xy(self, item, side: Side) -> tuple[float, float] | None:
        kind = item[0]
        if kind == "landmark":
            lm = next((x for x in self.controller.data.landmarks if x.id == item[1]), None)
            if lm is None:
                return None
            pt = lm.coord(side)
            if not pt:
                if side == Side.B and lm.has_a:
                    pred = self.controller.predict_b_work(lm.a_x, lm.a_y)
                    if pred:
                        return self.controller.view_xy(Side.B, *pred)
                if side == Side.A and lm.has_b:
                    pred = self.controller.predict_a_work(lm.b_x, lm.b_y)
                    if pred:
                        return self.controller.view_xy(Side.A, *pred)
                return None
            return self.controller.view_xy(side, *self.controller._work_xy(side, pt[0], pt[1]))
        if kind == "contour":
            c = next((x for x in self.controller.data.contours if x.id == item[1]), None)
            if c is None or c.side != side or not c.vertices:
                return None
            xs = [v.x for v in c.vertices]
            ys = [v.y for v in c.vertices]
            return self.controller.view_xy(side, *self.controller._work_xy(side, sum(xs) / len(xs), sum(ys) / len(ys)))
        if kind == "contour_node":
            c = next((x for x in self.controller.data.contours if x.id == item[1]), None)
            if c is None or c.side != side:
                return None
            idx = int(item[2])
            if idx < 0 or idx >= len(c.vertices):
                return None
            v = c.vertices[idx]
            return self.controller.view_xy(side, *self.controller._work_xy(side, v.x, v.y))
        if kind == "dimension":
            dim = next((x for x in self.controller.data.dimensions if x.id == item[1]), None)
            if dim is None or dim.side != side:
                return None
            return self.controller.view_xy(
                side,
                *self.controller._work_xy(side, (dim.p1x + dim.p2x) / 2.0, (dim.p1y + dim.p2y) / 2.0),
            )
        if kind == "mesh_edge":
            pts = self.controller.mesh_points(side)
            if item[1] not in pts or item[2] not in pts:
                return None
            a, b = pts[item[1]], pts[item[2]]
            return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        return None

    def _center_item(self, item, sides=None) -> None:
        if isinstance(item, str):
            item = ("landmark", item, None)
        if sides is None:
            if item[0] in ("contour", "contour_node", "dimension") and len(item) > 2:
                obj_side = None
                if item[0] == "contour":
                    c = next((x for x in self.controller.data.contours if x.id == item[1]), None)
                    obj_side = c.side if c else None
                elif item[0] == "dimension":
                    d = next((x for x in self.controller.data.dimensions if x.id == item[1]), None)
                    obj_side = d.side if d else None
                sides = (obj_side,) if obj_side else (Side.A, Side.B)
            else:
                sides = (Side.A, Side.B)
        z_a, z_b = self.view_a.current_zoom(), self.view_b.current_zoom()
        for side in sides:
            if side is None:
                continue
            xy = self._item_scene_xy(item, side)
            if xy is None:
                continue
            view = self.view_a if side == Side.A else self.view_b
            view.center_keep_zoom(xy[0], xy[1])
        if abs(self.view_a.current_zoom() - z_a) > 1e-6:
            self.view_a.resetTransform()
            self.view_a.scale(z_a, z_a)
        if abs(self.view_b.current_zoom() - z_b) > 1e-6:
            self.view_b.resetTransform()
            self.view_b.scale(z_b, z_b)

    def _navigate(self, target: str) -> None:
        if target.startswith("group:"):
            self.controller.active_group = target.split(":", 1)[1]
        self.statusBar().showMessage(f"Navigate: {target}")

    def _label_offset(self, placed, x, y, side):
        ox, oy = 8.0, -8.0
        for px, py, s in placed:
            if s != side:
                continue
            if abs(px - x) < 14 and abs(py - y) < 14:
                oy -= 12
        placed.append((x + ox, y + oy, side))
        return (ox, oy)

    def _selected_ids(self, kind: str) -> list[str]:
        return [s[1] for s in self._selection if s[0] == kind]

    def _set_selection(self, items, *, add: bool = False, pulse: bool = True) -> None:
        prev = list(self._selection)
        if not add:
            self._selection = list(items)
        else:
            for it in items:
                if it not in self._selection:
                    self._selection.append(it)
        self._announce_hidden_selection()
        self.refresh_overlays()
        if pulse and self._selection and self._selection != prev:
            self._start_selection_pulse()
        if bool(getattr(self.controller.data, "center_on_select", False)) and self._selection:
            self._center_item(self._selection[0], sides=None)

    def _announce_hidden_selection(self) -> None:
        from app.i18n import tr

        for s in self._selection:
            hidden = False
            if s[0] == "landmark":
                lm = next((x for x in self.controller.data.landmarks if x.id == s[1]), None)
                hidden = bool(lm and not getattr(lm, "visible", True))
            elif s[0] == "contour":
                c = next((x for x in self.controller.data.contours if x.id == s[1]), None)
                hidden = bool(c and not c.visible)
            elif s[0] == "dimension":
                d = next((x for x in self.controller.data.dimensions if x.id == s[1]), None)
                hidden = bool(d and not d.visible)
            if hidden:
                self.statusBar().showMessage(tr("sel.object_hidden"))
                return

    def _start_selection_pulse(self) -> None:
        self._pulse_left = 6
        self._selection_pulse = True
        self._pulse_timer.start()

    def _pulse_tick(self) -> None:
        self._pulse_left -= 1
        self._selection_pulse = not self._selection_pulse
        if self._pulse_left <= 0:
            self._selection_pulse = False
            self._pulse_timer.stop()
        self.refresh_overlays()

    def _hit_objects(self, side: Side, x: float, y: float) -> list[tuple]:
        """Work-space hit list, highest priority first."""
        view = self.view_a if side == Side.A else self.view_b
        z = max(abs(view.current_zoom()), 1e-6)
        rad = 10.0 / z
        hits: list[tuple] = []
        sel_ids = {s[1] for s in self._selection if s[0] == "landmark"}
        selected_first = []
        others = []
        for lm in self.controller.data.landmarks:
            if not getattr(lm, "visible", True):
                continue
            pt = lm.coord(side)
            if not pt:
                continue
            wx, wy = self.controller._work_xy(side, pt[0], pt[1])
            if math.hypot(wx - x, wy - y) <= rad:
                item = ("landmark", lm.id, side)
                (selected_first if lm.id in sel_ids else others).append(item)
        hits.extend(selected_first)
        hits.extend(others)
        chit = self._hit_contour(side, x, y, rad)
        if chit:
            c, idx, kind = chit
            hits.append(("contour_node", c.id, idx, kind))
            hits.append(("contour", c.id, None))
        for dim in self.controller.data.dimensions:
            if dim.side != side or not dim.visible:
                continue
            if math.hypot(dim.p1x - x, dim.p1y - y) <= rad or math.hypot(dim.p2x - x, dim.p2y - y) <= rad:
                hits.append(("dimension", dim.id, None))
        pts = self.controller.mesh_points(side)
        for e in self.controller.data.mesh.edges:
            if getattr(e, "excluded", False) or e.a not in pts or e.b not in pts:
                continue
            ax, ay = float(pts[e.a][0]), float(pts[e.a][1])
            bx, by = float(pts[e.b][0]), float(pts[e.b][1])
            if _seg_dist(x, y, ax, ay, bx, by) <= rad:
                hits.append(("mesh_edge", e.a, e.b))
        return hits

    def _delete_selected(self) -> None:
        from app.i18n import tr

        items = list(self._selection)
        if not items:
            pid = self.point_table._current_id()
            if pid:
                items = [("landmark", pid, None)]
        if not items:
            return
        lms = [s[1] for s in items if s[0] == "landmark"]
        for pid in lms:
            lm = next((x for x in self.controller.data.landmarks if x.id == pid), None)
            if lm and lm.point_type in (PointType.ZERO, PointType.SCALE):
                if not _offscreen() and QMessageBox.question(
                    self,
                    tr("sel.delete"),
                    tr("sel.delete_zero"),
                ) != QMessageBox.StandardButton.Yes:
                    return
                self.controller.reset_alignment(delete_controls=False)
                self._selection = []
                self.refresh_all()
                return
        neighbor = self._neighbor_after_delete(items)
        self.controller.delete_selection(items)
        self._selection = [neighbor] if neighbor else []
        self.refresh_overlays()
        self.refresh_chrome()

    def _neighbor_after_delete(self, items) -> tuple | None:
        keys = set(items)
        rows = list(getattr(self.point_table, "_rows", []))
        idxs = [i for i, r in enumerate(rows) if r in keys]
        if not idxs:
            return None
        last = max(idxs)
        for j in range(last + 1, len(rows)):
            if rows[j] not in keys:
                return rows[j]
        for j in range(min(idxs) - 1, -1, -1):
            if rows[j] not in keys:
                return rows[j]
        return None

    def _hide_selected(self) -> None:
        if self._selection:
            self.controller.set_objects_visible(self._selection, False)
        self.refresh_overlays()

    def _show_hidden(self) -> None:
        self.controller.show_all_hidden()
        self.refresh_overlays()

    def _reset_actual_selected(self) -> None:
        for s in self._selection:
            if s[0] == "landmark":
                self.controller.reset_actual(s[1])
        self.refresh_overlays()
        self.refresh_chrome()

    def _toggle_show_ids(self) -> None:
        mesh = self.controller.data.mesh
        mesh.show_ids = not mesh.show_ids
        if hasattr(self, "act_show_ids"):
            self.act_show_ids.setChecked(mesh.show_ids)
        self.refresh_overlays()

    def _nudge(self, side: Side, dx: float, dy: float) -> None:
        ids = self._selected_ids("landmark")
        if not ids:
            pid = self.point_table._current_id()
            if pid:
                ids = [pid]
        if not ids:
            return
        for pid in ids:
            lm = next((x for x in self.controller.data.landmarks if x.id == pid), None)
            if lm and lm.coord(side):
                self.controller.nudge_selected(lm, side, dx, dy)
        self.refresh_overlays()
        self.refresh_chrome()

    def _view_pt(self, side: Side, x: float, y: float) -> tuple[float, float]:
        return self.controller.view_xy(side, *self.controller._work_xy(side, x, y))

    def _dimension_click(self, side: Side, x: float, y: float) -> None:
        from app.i18n import tr

        self.tool_clicks.append((side, x, y))
        n = len([c for c in self.tool_clicks if c[0] == side])
        if n == 1:
            self.statusBar().showMessage(tr("status.dim_p2"))
        elif n == 2:
            self.statusBar().showMessage(tr("status.dim_off"))
        elif n >= 3:
            pts = [c for c in self.tool_clicks if c[0] == side]
            p1, p2, off = pts[-3], pts[-2], pts[-1]
            self.controller.add_dimension(side, (p1[1], p1[2]), (p2[1], p2[2]), (off[1], off[2]), orientation=self._dim_orientation)
            self.tool_clicks = [c for c in self.tool_clicks if c[0] != side]
            self.view_a.clear_preview()
            self.view_b.clear_preview()
            self.refresh_overlays()

    def _hit_contour(self, side: Side, x: float, y: float, radius: float | None = None):
        view = self.view_a if side == Side.A else self.view_b
        z = abs(view.current_zoom()) or 1.0
        radius = (8.0 / z) if radius is None else radius
        for c in self.controller.data.contours:
            if c.side != side:
                continue
            for i, v in enumerate(c.vertices):
                if math.hypot(v.x - x, v.y - y) <= radius:
                    return c, i, "node"
                if v.c1x is not None and v.c1y is not None and math.hypot(v.c1x - x, v.c1y - y) <= radius:
                    return c, i, "c1"
                if v.c2x is not None and v.c2y is not None and math.hypot(v.c2x - x, v.c2y - y) <= radius:
                    return c, i, "c2"
        return None

    def _contour_click(self, side: Side, x: float, y: float) -> None:
        hit = self._hit_contour(side, x, y)
        if hit:
            c, idx, kind = hit
            self._contour_edit = (c.id, idx, kind)
            return
        self._contour_edit = None
        open_c = next(
            (c for c in reversed(self.controller.data.contours) if c.side == side and not c.closed and c.source in ("manual", "proposal")),
            None,
        )
        if open_c and open_c.vertices:
            v0 = open_c.vertices[0]
            if math.hypot(x - v0.x, y - v0.y) < (8.0 / max(abs((self.view_a if side == Side.A else self.view_b).current_zoom()), 1e-6)) and len(open_c.vertices) >= 3:
                self.controller.close_active_contour(side)
                self.refresh_overlays()
                return
        self._contour_press = (side, x, y)
        self.controller._place_contour_vertex(side, x, y)
        self.refresh_overlays()

    def _crop_press(self, side: Side, x: float, y: float) -> None:
        view = self.view_a if side == Side.A else self.view_b
        self._crop_target = side
        handle = view.crop_handle_at(x, y)
        if handle and view._crop_draft_rect:
            self._crop_handle = (side, handle)
            self._crop_move = None
            self._crop_state[side] = "CROP_SELECTED"
            return
        if view.crop_body_at(x, y) and view._crop_draft_rect:
            rx, ry, rw, rh = view._crop_draft_rect
            self._crop_move = (side, x, y, (rx, ry, rw, rh))
            self._crop_handle = None
            self._crop_state[side] = "CROP_SELECTED"
            return
        self._crop_handle = None
        self._crop_move = None
        self._crop_draft = (side, x, y, x, y)
        self._crop_state[side] = "CROP_SELECTING"
        self._refresh_crop_chrome()

    def _preview_move(self, side: Side, x: float, y: float) -> None:
        tool = self.controller.active_tool
        if tool == ActiveTool.DIMENSION:
            pts = [c for c in self.tool_clicks if c[0] == side]
            if len(pts) == 2:
                from core.dimensions import annotation_line, format_length, measured_length_px
                from models.dimension import Dimension

                dim = Dimension(id="preview", side=side, p1x=pts[0][1], p1y=pts[0][2], p2x=pts[1][1], p2y=pts[1][2], orientation=self._dim_orientation)
                from core.dimensions import set_offset_from_click

                set_offset_from_click(dim, x, y)
                q1, q2, mid = annotation_line(dim)
                img = self.controller.image(side)
                scale = img.calibration.mm_per_px if img and img.calibration.is_calibrated else None
                unit = self.controller.data.display_unit
                text = format_length(measured_length_px(dim), scale, unit, dim.precision)
                view = self.view_a if side == Side.A else self.view_b
                view.clear_preview()
                view.add_dimension_graphic(self._view_pt(side, dim.p1x, dim.p1y), self._view_pt(side, dim.p2x, dim.p2y), self._view_pt(side, q1[0], q1[1]), self._view_pt(side, q2[0], q2[1]), self._view_pt(side, mid[0], mid[1]), text, preview=True)
        elif tool == ActiveTool.CROP:
            view = self.view_a if side == Side.A else self.view_b
            if self._crop_handle and self._crop_handle[0] == side:
                rect = view.resize_crop_draft(self._crop_handle[1], x, y)
                if rect:
                    view.clear_preview()
                    view.set_working_crop_overlay(*rect, preview=True)
                    self._crop_draft = (side, rect[0], rect[1], rect[0] + rect[2], rect[1] + rect[3])
                    self._crop_state[side] = "CROP_SELECTED"
                return
            if self._crop_move and self._crop_move[0] == side:
                _s, x0, y0, (rx, ry, rw, rh) = self._crop_move
                nx, ny = rx + (x - x0), ry + (y - y0)
                view.move_crop_draft(nx, ny, rw, rh)
                view.clear_preview()
                view.set_working_crop_overlay(nx, ny, rw, rh, preview=True)
                self._crop_draft = (side, nx, ny, nx + rw, ny + rh)
                return
            if self._crop_draft and self._crop_draft[0] == side:
                s, x0, y0, _, _ = self._crop_draft
                self._crop_draft = (s, x0, y0, x, y)
                view.clear_preview()
                rx, ry = min(x0, x), min(y0, y)
                view.set_working_crop_overlay(rx, ry, abs(x - x0), abs(y - y0), preview=True)
                if abs(x - x0) > 2 and abs(y - y0) > 2:
                    self._crop_state[side] = "CROP_SELECTED"

    def _drag_move(self, side: Side, x0: float, y0: float, x1: float, y1: float) -> None:
        if self.controller.active_tool == ActiveTool.CONTOUR and getattr(self, "_contour_edit", None):
            cid, idx, kind = self._contour_edit
            if kind == "node":
                self.controller.move_contour_vertex(cid, idx, x1, y1)
            else:
                self.controller.move_contour_handle(cid, idx, kind, x1, y1)
            self.refresh_overlays()
            return
        if self.controller.active_tool == ActiveTool.CROP:
            self._preview_move(side, x1, y1)
            return
        if self._moving and self.controller.active_tool in (ActiveTool.PAN, ActiveTool.SELECT, ActiveTool.CONTROL):
            pid, s, _old = self._moving
            if s == side:
                wx, wy = self.controller.unview_xy(side, x1, y1)
                ox, oy = self.controller._store_from_work(side, wx, wy)
                self.controller.preview_move_landmark(pid, s, ox, oy)
                self.refresh_overlays()
            return
        if self.controller.active_tool == ActiveTool.SELECT and abs(x1 - x0) > 4 and abs(y1 - y0) > 4:
            self._box_select(side, x0, y0, x1, y1)

    def _box_select(self, side: Side, x0: float, y0: float, x1: float, y1: float) -> None:
        wx0, wy0 = self.controller.unview_xy(side, min(x0, x1), min(y0, y1))
        wx1, wy1 = self.controller.unview_xy(side, max(x0, x1), max(y0, y1))
        xlo, xhi = min(wx0, wx1), max(wx0, wx1)
        ylo, yhi = min(wy0, wy1), max(wy0, wy1)
        found = []
        for lm in self.controller.data.landmarks:
            pt = lm.coord(side)
            if not pt:
                continue
            wx, wy = self.controller._work_xy(side, pt[0], pt[1])
            if xlo <= wx <= xhi and ylo <= wy <= yhi:
                found.append(("landmark", lm.id, side))
        add = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier)
        self._set_selection(found, add=add)

    def _released(self, side: Side, x: float, y: float, dragged: bool) -> None:
        if self._moving:
            pid, s, old = self._moving
            if s == side and old is not None:
                lm = next((x for x in self.controller.data.landmarks if x.id == pid), None)
                if lm is not None:
                    new = lm.coord(s)
                    self.controller.commit_move_landmark(pid, s, old, new)
            self._moving = None
        if self.controller.active_tool == ActiveTool.CONTOUR and getattr(self, "_contour_edit", None):
            cid, idx, kind = self._contour_edit
            if kind == "node":
                self.controller.move_contour_vertex(cid, idx, x, y)
            else:
                self.controller.move_contour_handle(cid, idx, kind, x, y)
            self._contour_edit = None
            self.refresh_overlays()
            return
        if self.controller.active_tool == ActiveTool.CONTOUR and dragged and self._contour_press:
            sx, sy = self._contour_press[1], self._contour_press[2]
            open_c = next(
                (c for c in reversed(self.controller.data.contours) if c.side == side and not c.closed),
                None,
            )
            if open_c and len(open_c.vertices) >= 2:
                prev = open_c.vertices[-2]
                last = open_c.vertices[-1]
                prev.kind = "cubic"
                prev.c2x = sx + (x - sx)
                prev.c2y = sy + (y - sy)
                last.c1x = last.x - (x - sx)
                last.c1y = last.y - (y - sy)
                last.node_type = "SMOOTH"
                self.controller.mark_dirty()
                self.refresh_overlays()
            self._contour_press = None
        if self.controller.active_tool == ActiveTool.CROP:
            self._crop_handle = None
            self._crop_move = None
            if self._crop_draft:
                s, x0, y0, x1, y1 = self._crop_draft
                if self._crop_state.get(s) == "CROP_SELECTING":
                    self._crop_draft = (s, x0, y0, x, y)
                    x1, y1 = x, y
                view = self.view_a if s == Side.A else self.view_b
                view.clear_preview()
                rx, ry = min(x0, x1), min(y0, y1)
                rw, rh = abs(x1 - x0), abs(y1 - y0)
                if rw >= 2 and rh >= 2:
                    self._crop_draft = (s, rx, ry, rx + rw, ry + rh)
                    view.set_working_crop_overlay(rx, ry, rw, rh, preview=True)
                    self._crop_state[s] = "CROP_SELECTED"
                    self._refresh_crop_chrome()

    def _double_click(self, side: Side, x: float, y: float) -> None:
        if self.controller.active_tool == ActiveTool.CONTOUR:
            self.controller.finish_open_contour(side)
            self.set_tool(ActiveTool.PAN)

    def _crop_draft_from_view(self, side: Side | None):
        if self._crop_draft and (side is None or self._crop_draft[0] == side):
            return self._crop_draft
        views = ((Side.A, self.view_a), (Side.B, self.view_b))
        for s, view in views:
            if side is not None and s != side:
                continue
            if view._crop_draft_rect:
                x, y, w, h = view._crop_draft_rect
                return (s, x, y, x + w, y + h)
        return None

    def _view_rect_to_original(self, side: Side, x0: float, y0: float, x1: float, y1: float):
        pts = []
        for vx, vy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
            wx, wy = self.controller.unview_xy(side, vx, vy)
            ox, oy = self.controller._store_from_work(side, wx, wy)
            pts.append((ox, oy))
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)

    def _apply_crop(self, side: Side | None = None) -> None:
        draft = self._crop_draft_from_view(side)
        if not draft:
            return
        side, x0, y0, x1, y1 = draft
        vx, vy = min(x0, x1), min(y0, y1)
        vw, vh = abs(x1 - x0), abs(y1 - y0)
        if vw < 2 or vh < 2:
            return
        ox, oy, ow, oh = self._view_rect_to_original(side, vx, vy, vx + vw, vy + vh)
        if ow < 2 or oh < 2:
            return
        self.controller.set_crop(side, ox, oy, ow, oh)
        self._crop_draft = None
        self._crop_handle = None
        self._crop_move = None
        self._crop_state[side] = "CROP_APPLIED"
        view = self.view_a if side == Side.A else self.view_b
        view.clear_preview()
        view.hide_crop_hud()
        view.set_working_region(self.controller.crop_view_rect(side), fit=True)
        self.set_tool(ActiveTool.PAN)
        self._refresh_crop_chrome()
        self._refresh_mode_ui()
        from app.i18n import tr

        self.statusBar().showMessage(tr("st.crop") + "  " + tr("crop.target_a" if side == Side.A else "crop.target_b"))

    def _cancel_crop(self, side: Side | None = None) -> None:
        target = side or self._crop_target
        self._crop_draft = None
        self._crop_handle = None
        self._crop_move = None
        for s, view in ((Side.A, self.view_a), (Side.B, self.view_b)):
            if target is not None and s != target:
                continue
            view.clear_preview()
            view.hide_crop_hud()
            if self._crop_state.get(s) != "CROP_APPLIED":
                img = self.controller.image(s)
                if img is None or img.crop_rect() is None:
                    self._crop_state[s] = "CROP_OFF"
        self.set_tool(ActiveTool.PAN)
        self._refresh_crop_chrome()

    def _reset_crop(self, side: Side | None = None) -> None:
        sides = (side,) if side is not None else (Side.A, Side.B)
        for s in sides:
            self.controller.reset_crop(s)
            self._crop_state[s] = "CROP_OFF"
            view = self.view_a if s == Side.A else self.view_b
            view.clear_preview()
            view.hide_crop_hud()
            view.set_working_region(None, fit=True)
        self._crop_draft = None
        self._crop_handle = None
        self._crop_move = None
        self.refresh_images()
        self._refresh_crop_chrome()
        self._refresh_mode_ui()

    def _edit_crop(self, side: Side | None = None) -> None:
        target = side or self._crop_target
        if target is None:
            for s in (Side.A, Side.B):
                if self.controller.image(s) and self.controller.image(s).crop_rect():
                    target = s
                    break
        if target is None:
            self.set_tool(ActiveTool.CROP)
            return
        rect = self.controller.crop_view_rect(target)
        if rect is None:
            self.set_tool(ActiveTool.CROP)
            return
        self.set_tool(ActiveTool.CROP)
        self._crop_target = target
        x, y, w, h = rect
        self._crop_draft = (target, x, y, x + w, y + h)
        self._crop_state[target] = "CROP_SELECTED"
        view = self.view_a if target == Side.A else self.view_b
        view.set_working_region(None, fit=False)
        view.clear_preview()
        view.set_working_crop_overlay(x, y, w, h, preview=True)
        self._refresh_crop_chrome()

    def _refresh_crop_chrome(self) -> None:
        from app.i18n import tr

        tool_on = self.controller.active_tool == ActiveTool.CROP
        applied = False
        for s in (Side.A, Side.B):
            img = self.controller.image(s)
            if img is not None and img.crop_rect():
                applied = True
                if self._crop_state.get(s) == "CROP_OFF":
                    self._crop_state[s] = "CROP_APPLIED"
        if hasattr(self, "toolbar"):
            self.toolbar.apply_crop_btn.setVisible(tool_on)
            self.toolbar.cancel_crop_btn.setVisible(tool_on)
            self.toolbar.reset_crop_btn.setVisible(tool_on or applied)
            if hasattr(self.toolbar, "edit_crop_btn"):
                self.toolbar.edit_crop_btn.setVisible(applied)
        if tool_on:
            if self._crop_target == Side.A:
                self.statusBar().showMessage(tr("crop.target_a") + "  —  " + tr("status.crop"))
            elif self._crop_target == Side.B:
                self.statusBar().showMessage(tr("crop.target_b") + "  —  " + tr("status.crop"))
            else:
                self.statusBar().showMessage(tr("status.crop"))

    def _draw_dimensions(self) -> None:
        from core.dimensions import annotation_line, format_length, measured_length_px
        from models.enums import Unit

        if not self.controller.data.show_dimensions:
            return
        for dim in self.controller.data.dimensions:
            if not dim.visible:
                continue
            view = self.view_a if dim.side == Side.A else self.view_b
            q1, q2, mid = annotation_line(dim)
            img = self.controller.image(dim.side)
            scale = img.calibration.mm_per_px if img and img.calibration.is_calibrated else None
            unit = self.controller.data.display_unit
            text = format_length(measured_length_px(dim), scale, unit, dim.precision)
            if dim.label and dim.label != dim.id:
                text = f"{dim.label}  {text}"
            view.add_dimension_graphic(
                self._view_pt(dim.side, dim.p1x, dim.p1y),
                self._view_pt(dim.side, dim.p2x, dim.p2y),
                self._view_pt(dim.side, q1[0], q1[1]),
                self._view_pt(dim.side, q2[0], q2[1]),
                self._view_pt(dim.side, mid[0], mid[1]),
                text,
                selected=any(s[0] == "dimension" and s[1] == dim.id for s in self._selection),
            )

    def retranslate_ui(self) -> None:
        from app.i18n import tr, tr_menu

        self.setWindowTitle(tr("app.title"))
        if hasattr(self, "toolbar"):
            self.toolbar.retranslate()
        if hasattr(self, "point_table"):
            self.point_table.retranslate()
        for act in self._all_actions:
            en = act.property("en_text")
            if en:
                act.setText(tr_menu(str(en)))
        for act in self.menuBar().actions():
            en = act.property("en_text") or act.text()
            act.setProperty("en_text", en)
            act.setText(tr_menu(str(en)))
            sub = act.menu()
            if sub is not None:
                for child in sub.actions():
                    if child.isSeparator():
                        continue
                    cen = child.property("en_text") or child.text()
                    child.setProperty("en_text", cen)
                    child.setText(tr_menu(str(cen)))
                    sm = child.menu()
                    if sm is not None:
                        for g in sm.actions():
                            if g.isSeparator():
                                continue
                            gen = g.property("en_text") or g.text()
                            g.setProperty("en_text", gen)
                            g.setText(tr_menu(str(gen)))
        if hasattr(self, "act_undo"):
            self.act_undo.setText(tr("tb.undo"))
            self.act_redo.setText(tr("tb.redo"))
        self._sync_scale_status()
        for dock in self.docks.values():
            en = dock.property("en_title")
            if en:
                dock.setWindowTitle(tr_menu(str(en)))
        self.view_a.crop_hud.retranslate()
        self.view_b.crop_hud.retranslate()
        if hasattr(self, "adjust_panel") and hasattr(self.adjust_panel, "retranslate"):
            self.adjust_panel.retranslate()
        if hasattr(self, "contour_panel") and hasattr(self.contour_panel, "retranslate"):
            self.contour_panel.retranslate()
        if hasattr(self, "overlay_bar"):
            self.overlay_bar.retranslate()
        self._refresh_mode_ui()

    def _zoom_both(self, factor: float) -> None:
        self.view_a.resetTransform()
        self.view_b.resetTransform()
        self.view_a.scale(factor, factor)
        self.view_b.scale(factor, factor)
        self.view_a.render_mode = "inspect" if factor >= 1.0 else "fit_hq"
        self.view_b.render_mode = self.view_a.render_mode
        self.view_a._apply_interpolation()
        self.view_b._apply_interpolation()

    def _edit_last_dimension(self) -> None:
        if not self.controller.data.dimensions:
            QMessageBox.information(self, "Dimension", "Create a dimension first.")
            return
        from ui.dimension_dialog import DimensionEditDialog

        DimensionEditDialog(self.controller, self.controller.data.dimensions[-1].id, self).exec()
        self.refresh_overlays()

    def _project_last_contour(self) -> None:
        src = next((c for c in reversed(self.controller.data.contours) if c.side == Side.A and c.source != "projected"), None)
        if src is None:
            QMessageBox.information(self, "Contour", "Draw a contour on A first.")
            return
        self.controller.project_contour_to_other(src.id)
        self.refresh_overlays()

    def _project_last_dimension(self) -> None:
        src = next((d for d in reversed(self.controller.data.dimensions) if d.side == Side.A), None)
        if src is None:
            QMessageBox.information(self, "Dimension", "Create a dimension on A first.")
            return
        self.controller.project_dimension_to_other(src.id)
        self.refresh_overlays()

    def _quality_test(self) -> None:
        from app.i18n import tr

        view = self.view_a if self.controller.image(Side.A) is not None else self.view_b
        view.zoom_100()
        report = view.quality_crop_compare(100, 100, 300, 300)
        diag = view.quality_diag()
        text = (
            f"SOURCE DIMENSIONS: {diag.get('source')}\n"
            f"SOURCE PIXEL FORMAT: {diag.get('source_format')}\n"
            f"DECODED IMAGE DIMENSIONS: {diag.get('decoded')}\n"
            f"PROCESSED IMAGE DIMENSIONS: {diag.get('processed')}\n"
            f"QIMAGE DIMENSIONS: {diag.get('qimage')}\n"
            f"QPIXMAP DIMENSIONS: {diag.get('pixmap')}\n"
            f"SCENE ITEM DIMENSIONS: {diag.get('scene')}\n"
            f"VIEW SCALE: {diag.get('zoom')}\n"
            f"DEVICE PIXEL RATIO: {diag.get('dpr')}\n"
            f"WINDOWS DPI: {diag.get('windows_dpi')}\n"
            f"INTERPOLATION MODE: {diag.get('interpolation')}\n"
            f"RENDERING FROM ORIGINAL: {diag.get('rendering_from_original')}\n"
            f"CROP 100..300 MATCH: {report}\n"
        )
        QMessageBox.information(self, tr("quality.test"), text)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if is_live_text_editor(QApplication.focusWidget()):
            super().keyPressEvent(event)
            return
        if event.key() == Qt.Key.Key_Backspace:
            self._delete_selected()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            if self._cancel_unfinished():
                event.accept()
                return
            if self.controller.active_tool not in (ActiveTool.PAN, ActiveTool.SELECT):
                self.set_tool(ActiveTool.PAN)
                event.accept()
                return
            if self._selection:
                self._set_selection([], pulse=False)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Space:
            self._space_down = True
            self.view_a._space_pan = True
            self.view_b._space_pan = True
            if self._overlay_workspace:
                view = self.view_b if overlay_base_side(self.controller.data.overlay_mode) == Side.B else self.view_a
                view.set_base_opacity(0.0)
                view.set_layer_opacity(1.0)
            elif self.controller.data.overlay_mode == OverlayMode.FLICKER:
                self.refresh_images()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Tab:
            self.image_workspace()
            event.accept()
            return
        if event.key() == Qt.Key.Key_F11:
            self.toggle_fullscreen()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.controller.active_tool == ActiveTool.CONTOUR:
                self.controller.finish_open_contour(Side.A)
                self.controller.finish_open_contour(Side.B)
                self.set_tool(ActiveTool.PAN)
                event.accept()
                return
            if self.controller.active_tool == ActiveTool.PITCH and len(self.tool_clicks) >= 2:
                arr = np.array([[p[1], p[2]] for p in self.tool_clicks], dtype=np.float64)
                QMessageBox.information(self, "Pitch", str(pitch_series(arr)))
                self.tool_clicks.clear()
                event.accept()
                return
            if self.controller.active_tool == ActiveTool.CIRCLE and len(self.tool_clicks) >= 3:
                arr = np.array([[p[1], p[2]] for p in self.tool_clicks], dtype=np.float64)
                try:
                    QMessageBox.information(self, "Circle fit", str(fit_circle(arr)))
                except ValueError as exc:
                    QMessageBox.warning(self, "Circle", str(exc))
                self.tool_clicks.clear()
                event.accept()
                return
        # Documented nudge: Arrow=1 px, Shift+Arrow=10 px, Ctrl+Arrow=0.1 px (source image).
        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:
            step = 0.1
        elif mods & Qt.KeyboardModifier.ShiftModifier:
            step = 10.0
        else:
            step = 1.0
        mapping = {
            Qt.Key.Key_Left: (-step, 0.0),
            Qt.Key.Key_Right: (step, 0.0),
            Qt.Key.Key_Up: (0.0, -step),
            Qt.Key.Key_Down: (0.0, step),
        }
        if event.key() in mapping:
            side = Side.A
            if self._selection and len(self._selection[0]) >= 3 and self._selection[0][2] in (Side.A, Side.B):
                side = self._selection[0][2]
            self._nudge(side, *mapping[event.key()])
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Space:
            self._space_down = False
            self.view_a._space_pan = False
            self.view_b._space_pan = False
            if self._overlay_workspace:
                self._overlay_opacity(int(self.controller.data.overlay_opacity * 100))
            elif self.controller.data.overlay_mode == OverlayMode.FLICKER:
                self.refresh_images()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        import os

        app = QApplication.instance()
        if app is not None and getattr(self, "_undo_filter", None) is not None:
            app.removeEventFilter(self._undo_filter)
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            event.accept()
            return
        if self.controller.dirty:
            ans = QMessageBox.question(
                self,
                "Quit",
                "Save project before exit?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            )
            if ans == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if ans == QMessageBox.StandardButton.Save:
                self._save()
        self.controller.autosave_recovery()
        settings = qt_settings()
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("windowState", self.saveState())
        settings.setValue("layout_version", LAYOUT_VERSION)
        settings.setValue("splitter", self.workspace_split.saveState())
        event.accept()

    # ----- layout / workspace -----
    def _apply_or_restore_layout(self) -> None:
        import os

        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            self.reset_layout()
            return
        settings = qt_settings()
        version = int(settings.value("layout_version", 0) or 0)
        geo = settings.value("geometry")
        if geo and version == LAYOUT_VERSION:
            self.restoreGeometry(geo)
            self._clamp_to_screen()
            state = settings.value("windowState")
            if state:
                self.restoreState(state)
            split = settings.value("splitter")
            if split:
                self.workspace_split.restoreState(split)
            return
        self.reset_layout()

    def _clamp_to_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        geo = self.frameGeometry()
        if geo.width() > avail.width() or geo.height() > avail.height():
            self.resize(min(self.width(), avail.width() - 40), min(self.height(), avail.height() - 40))
        if not avail.contains(self.frameGeometry().center()):
            self.move(avail.x() + 20, avail.y() + 20)

    def reset_layout(self) -> None:
        for dock in {d for d in self.docks.values()}:
            dock.hide()
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.pane_a.show()
        self.pane_b.show()
        self.workspace_split.setSizes([500, 500])
        self.workflow.show()
        self._docks_hidden = False
        self._size_to_screen()
        self._clamp_to_screen()

    def image_workspace(self) -> None:
        visible = [d for d in set(self.docks.values()) if d.isVisible()]
        if visible and not self._docks_hidden:
            self._saved_dock_state = self.saveState()
            for d in set(self.docks.values()):
                d.hide()
            self.workflow.hide()
            self._docks_hidden = True
        else:
            if self._saved_dock_state is not None:
                self.restoreState(self._saved_dock_state)
            self.workflow.show()
            self._docks_hidden = False

    def show_dock(self, key: str) -> None:
        dock = self.docks.get(key)
        if dock is None:
            return
        dock.show()
        dock.raise_()

    def set_workspace(self, mode: str) -> None:
        if mode == "A":
            self.pane_a.show()
            self.pane_b.hide()
            self.controller.data.overlay_mode = OverlayMode.A_ONLY
        elif mode == "B":
            self.pane_a.hide()
            self.pane_b.show()
            self.controller.data.overlay_mode = OverlayMode.B_ONLY
        else:
            self.pane_a.show()
            self.pane_b.show()
            self.controller.data.overlay_mode = OverlayMode.A_ONLY
            self.workspace_split.setSizes([500, 500])
        self.refresh_images()

    def set_overlay(self, mode: OverlayMode) -> None:
        if overlay_is_registered(mode):
            self.show_overlay_mode(mode)
            return
        if mode == OverlayMode.DIFFERENCE and self.controller.data.overlay_mode == OverlayMode.DIFFERENCE:
            self.close_overlay()
            return
        if mode == OverlayMode.EDGE_DIFFERENCE and self.controller.data.overlay_mode == OverlayMode.EDGE_DIFFERENCE:
            self.close_overlay()
            return
        if mode == OverlayMode.FLICKER and self.controller.data.overlay_mode == OverlayMode.FLICKER:
            self.close_overlay()
            return
        if mode in (OverlayMode.DIFFERENCE, OverlayMode.EDGE_DIFFERENCE, OverlayMode.FLICKER):
            self._last_diff_mode = OverlayMode.A_ONLY
        self.close_overlay()
        self.pane_a.show()
        self.pane_b.show()
        self.controller.data.overlay_mode = mode
        self.refresh_images()
        self._refresh_mode_ui()

    def show_overlay_mode(self, mode: OverlayMode | None = None) -> None:
        rec = self.controller.accepted_transform()
        if rec is None:
            from app.i18n import tr

            QMessageBox.warning(self, tr("ov.title"), tr("ov.need_align"))
            return
        if mode is None:
            mode = self._last_overlay_mode or OverlayMode.B_OVER_A
        if mode == OverlayMode.OVERLAY:
            mode = OverlayMode.B_OVER_A
        self._last_overlay_mode = mode
        self.controller.data.overlay_mode = mode
        self._overlay_workspace = True
        base = overlay_base_side(mode)
        if base == Side.A:
            self.pane_a.show()
            self.pane_b.hide()
        else:
            self.pane_b.show()
            self.pane_a.hide()
        self.overlay_bar.show()
        self.overlay_bar.set_opacity(int(self.controller.data.overlay_opacity * 100))
        self.overlay_bar.set_direction(mode)
        self.refresh_images()
        self._refresh_mode_ui()
        from app.i18n import tr

        self.statusBar().showMessage(tr("ov.status_on", dir=tr("ov.a_over_b") if mode == OverlayMode.A_OVER_B else tr("ov.b_over_a")))

    def _set_overlay_direction(self, mode: OverlayMode) -> None:
        self.show_overlay_mode(mode)

    def toggle_overlay(self) -> None:
        if self._overlay_workspace or overlay_is_registered(self.controller.data.overlay_mode):
            self.close_overlay()
        else:
            self.show_overlay_mode(self._last_overlay_mode)

    def close_overlay(self) -> None:
        if overlay_is_registered(self.controller.data.overlay_mode):
            self._last_overlay_mode = (
                OverlayMode.B_OVER_A
                if self.controller.data.overlay_mode == OverlayMode.OVERLAY
                else self.controller.data.overlay_mode
            )
        self._overlay_workspace = False
        self.controller.data.overlay_mode = OverlayMode.A_ONLY
        self.overlay_bar.hide()
        self.pane_a.show()
        self.pane_b.show()
        self.view_a.clear_registered_layer()
        self.view_b.clear_registered_layer()
        self.view_a.set_base_opacity(1.0)
        self.view_b.set_base_opacity(1.0)
        if self.controller.data.mesh.view_mode != MeshViewMode.SKELETON.value:
            self.view_a.set_image_opacity(1.0)
            self.view_b.set_image_opacity(1.0)
        self._flicker_timer.stop()
        self.refresh_images()
        self._refresh_mode_ui()

    def _overlay_opacity(self, percent: int) -> None:
        self.controller.data.overlay_opacity = max(0.0, min(1.0, percent / 100.0))
        if not (self._overlay_workspace or overlay_is_registered(self.controller.data.overlay_mode)):
            return
        view = self.view_b if overlay_base_side(self.controller.data.overlay_mode) == Side.B else self.view_a
        view.set_base_opacity(1.0)
        view.set_layer_opacity(percent / 100.0)

    def _overlay_preset(self, key: str) -> None:
        if key in ("a", "op0"):
            self.overlay_bar.set_opacity(0)
            self._overlay_opacity(0)
        elif key in ("mid", "op50", "reset_op"):
            self.overlay_bar.set_opacity(50)
            self._overlay_opacity(50)
        elif key == "op25":
            self.overlay_bar.set_opacity(25)
            self._overlay_opacity(25)
        elif key == "op75":
            self.overlay_bar.set_opacity(75)
            self._overlay_opacity(75)
        elif key in ("b", "op100"):
            self.overlay_bar.set_opacity(100)
            self._overlay_opacity(100)
        elif key == "flicker":
            self.set_overlay(OverlayMode.FLICKER)

    def fit_both(self) -> None:
        self.view_a.fit_image()
        self.view_b.fit_image()

    def actual_size_both(self) -> None:
        self.view_a.zoom_100()
        self.view_b.zoom_100()

    def reset_views(self) -> None:
        self.view_a.reset_view()
        self.view_b.reset_view()

    def _toggle_set_scale(self) -> None:
        if self.controller.active_tool == ActiveTool.CALIBRATION:
            self.set_tool(ActiveTool.PAN)
            return
        self._start_set_scale()

    def _toggle_mesh_or_build(self) -> None:
        mesh = self.controller.data.mesh
        if not mesh.edges:
            self._build_mesh()
            self._refresh_mode_ui()
            return
        self._toggle_mesh()
        self._refresh_mode_ui()

    def _toggle_skeleton(self) -> None:
        mesh = self.controller.data.mesh
        if mesh.view_mode == MeshViewMode.SKELETON.value:
            self._set_mesh_mode(self._last_mesh_mode if self._last_mesh_mode != MeshViewMode.SKELETON else MeshViewMode.GHOST)
        else:
            if mesh.view_mode != MeshViewMode.HIDDEN.value:
                self._last_mesh_mode = MeshViewMode(mesh.view_mode)
            self._set_mesh_mode(MeshViewMode.SKELETON)
        self._refresh_mode_ui()

    def _refresh_mode_ui(self) -> None:
        from app.i18n import tr

        mode = self.controller.data.overlay_mode
        ov_on = self._overlay_workspace or overlay_is_registered(mode)
        if hasattr(self, "toolbar") and hasattr(self.toolbar, "overlay_btn"):
            btn = self.toolbar.overlay_btn
            btn.setChecked(ov_on)
            if mode == OverlayMode.A_OVER_B:
                btn.setText(tr("tb.overlay_ab"))
            elif ov_on:
                btn.setText(tr("tb.overlay_ba"))
            else:
                btn.setText(tr("tb.overlay"))
        mesh_on = self.controller.data.mesh.view_mode != MeshViewMode.HIDDEN.value and bool(self.controller.data.mesh.edges)
        if hasattr(self, "toolbar") and hasattr(self.toolbar, "mesh_btn"):
            self.toolbar.mesh_btn.setChecked(mesh_on)
            act = self.toolbar.mesh_btn.defaultAction()
            if act:
                act.setChecked(mesh_on)
        sync_on = bool(self.controller.sync_zoom or self.controller.sync_pan)
        if hasattr(self, "toolbar") and hasattr(self.toolbar, "sync_btn"):
            self.toolbar.sync_btn.setChecked(sync_on)
            self.toolbar.act_sync_on.setChecked(sync_on)
        if hasattr(self, "toolbar") and hasattr(self.toolbar, "set_scale_btn"):
            self.toolbar.set_scale_btn.setChecked(self.controller.active_tool == ActiveTool.CALIBRATION)
        if hasattr(self, "act_diff"):
            self.act_diff.setChecked(mode == OverlayMode.DIFFERENCE)
            self.act_edge.setChecked(mode == OverlayMode.EDGE_DIFFERENCE)
            self.act_flicker.setChecked(mode == OverlayMode.FLICKER)
        if hasattr(self, "act_magnifier"):
            self.act_magnifier.setChecked(bool(self.controller.data.magnifier_on))
        if hasattr(self, "toolbar") and hasattr(self.toolbar, "act_ov_off"):
            self.toolbar.act_ov_off.setChecked(not ov_on)
            self.toolbar.act_ov_ab.setChecked(mode == OverlayMode.A_OVER_B)
            self.toolbar.act_ov_ba.setChecked(ov_on and mode in (OverlayMode.B_OVER_A, OverlayMode.OVERLAY))
        snap = self.modes.snapshot()
        parts = []
        if ov_on:
            parts.append(tr("st.overlay_ab") if mode == OverlayMode.A_OVER_B else tr("st.overlay_ba"))
        elif mode == OverlayMode.DIFFERENCE:
            parts.append(tr("st.diff"))
        elif mode == OverlayMode.EDGE_DIFFERENCE:
            parts.append(tr("st.edge"))
        if sync_on:
            parts.append(tr("st.sync"))
        if mesh_on:
            parts.append(tr("st.mesh"))
        if snap["skeleton_mode"]:
            parts.append(tr("st.skeleton"))
        if snap["magnifier_mode"][0]:
            parts.append(tr("st.mag", n=snap["magnifier_mode"][1]))
        enhance = snap["enhancement_mode"]
        if enhance[0] and enhance[0] != "OFF":
            parts.append(tr("st.enhance"))
        elif enhance[1] and enhance[1] != "OFF":
            parts.append(tr("st.enhance"))
        if any(self.controller.image(s) and self.controller.image(s).crop_rect() for s in (Side.A, Side.B)):
            parts.append(tr("st.crop"))
        if hasattr(self, "st_modes"):
            self.st_modes.setText("  ".join(parts))
        self._refresh_orient_buttons()

    def toggle_sync(self, checked: bool = False) -> None:
        on = bool(checked) if isinstance(checked, bool) else not (self.controller.sync_zoom or self.controller.sync_pan)
        self.controller.sync_zoom = on
        self.controller.sync_pan = on
        if on and self.controller.accepted_transform() is not None:
            self.controller.sync_cursor = True
        self.act_sync_zoom.setChecked(on)
        self.act_sync_pan.setChecked(on)
        if hasattr(self.toolbar, "sync_btn"):
            self.toolbar.sync_btn.setChecked(on)
            self.toolbar.act_sync_on.setChecked(on)
            self.toolbar.act_sync_zoom.setChecked(self.controller.sync_zoom)
            self.toolbar.act_sync_pan.setChecked(self.controller.sync_pan)
            self.toolbar.act_sync_cursor.setChecked(self.controller.sync_cursor)
        self._refresh_mode_ui()

    def toggle_lock(self) -> None:
        rec = self.controller.data.current_transform
        if rec and rec.status == TransformStatus.LOCKED:
            self._unlock()
        else:
            self._lock()

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # ----- clipboard / drop -----
    def paste_image(self, side: Side) -> None:
        clip = QApplication.clipboard()
        mime = clip.mimeData()
        if mime is not None and mime.hasImage():
            img = clip.image()
            if img is not None and not img.isNull():
                self.controller.load_image_from_qimage(side, img, label="Clipboard image")
                self._last_a_shape = self._last_b_shape = None
                self.refresh_images()
                return
        if mime is not None and mime.hasUrls():
            for url in mime.urls():
                path = url.toLocalFile()
                if path and Path(path).suffix.lower() in RASTER_EXTENSIONS:
                    self.controller.load_image(side, path)
                    self._last_a_shape = self._last_b_shape = None
                    self.refresh_images()
                    return
        QMessageBox.warning(self, "Paste", "Clipboard does not contain an image. Use Win+Shift+S, then Ctrl+V.")

    def paste_image_smart(self) -> None:
        focus = QApplication.focusWidget()
        if self.view_a is focus or self.pane_a.isAncestorOf(focus) if focus else False:
            self.paste_image(Side.A)
            return
        if self.view_b is focus or (focus and self.pane_b.isAncestorOf(focus)):
            self.paste_image(Side.B)
            return
        choice, ok = QInputDialog.getItem(self, "Paste image", "Paste to:", ["A", "B"], 0, False)
        if ok:
            self.paste_image(Side.A if choice == "A" else Side.B)

    def _drop_files(self, side: Side, paths: list) -> None:
        for path in paths:
            if Path(path).suffix.lower() in RASTER_EXTENSIONS:
                self.controller.load_image(side, path)
                self._last_a_shape = self._last_b_shape = None
                self.refresh_images()
                return
        QMessageBox.warning(self, "Drop", "Dropped file is not a supported image format.")

    def _drop_qimage(self, side: Side, image) -> None:
        if isinstance(image, QImage) and not image.isNull():
            self.controller.load_image_from_qimage(side, image, label="Dropped image")
            self._last_a_shape = self._last_b_shape = None
            self.refresh_images()

    def _update_pane_captions(self) -> None:
        for side, pane in ((Side.A, self.pane_a), (Side.B, self.pane_b)):
            img = self.controller.image(side)
            if img is None or img.array is None:
                pane.show_empty()
                continue
            if img.source_path:
                caption = Path(img.source_path).name
            elif img.notes:
                caption = img.notes
            else:
                caption = "Clipboard image"
            caption += f"  {img.width}×{img.height}"
            pane.show_image(caption)
            bits = [img.orientation.status_label()]
            if img.view_flip_x or img.view_flip_y:
                d = []
                if img.view_flip_x:
                    d.append("DISPLAY X")
                if img.view_flip_y:
                    d.append("DISPLAY Y")
                bits.append("(" + ", ".join(d) + ")")
            pane.set_orientation_text("  ".join(bits))

    def _context(self, side: Side, cmd: str, x: float, y: float) -> None:
        if cmd == "paste":
            self.paste_image(side)
        elif cmd == "fit":
            (self.view_a if side == Side.A else self.view_b).fit_image()
        elif cmd == "actual":
            (self.view_a if side == Side.A else self.view_b).zoom_100()
        elif cmd == "ref":
            self.set_tool(ActiveTool.REFERENCE)
            self.controller.place_point(side, x, y)
            self.refresh_overlays()
        elif cmd == "meas":
            self.set_tool(ActiveTool.MEASUREMENT)
            self.controller.place_point(side, x, y)
            self.refresh_overlays()
        elif cmd == "replace":
            self._load_image(side)
        elif cmd == "remove":
            self._remove_image(side)
        elif cmd == "zero":
            self.set_tool(ActiveTool.ZERO)
            self.controller.place_point(side, x, y)
            self.refresh_overlays()
        elif cmd == "scale":
            self.set_tool(ActiveTool.SCALE)
            self.controller.place_point(side, x, y)
            self.refresh_overlays()
        elif cmd == "control":
            self.set_tool(ActiveTool.CONTROL)
            self.controller.place_point(side, x, y)
            self.refresh_overlays()
        elif cmd == "copy":
            QApplication.clipboard().setText(f"{x:.4f}, {y:.4f}")
            self.statusBar().showMessage(f"Copied {x:.4f}, {y:.4f}")
        elif cmd == "reset_crop":
            self._reset_crop(side)
        elif cmd == "edit_crop":
            self._edit_crop(side)
        elif cmd == "crop":
            self.set_tool(ActiveTool.CROP)
            self._crop_target = side
            self._refresh_crop_chrome()
        elif cmd == "reset_view":
            (self.view_a if side == Side.A else self.view_b).reset_view()
        elif cmd == "overlay_off":
            self.close_overlay()

    def _import_pcb_cad(self) -> None:
        from app.i18n import tr
        from importers.pcb_import import PcbImportError, load_pcb, detect_kind, UNSUPPORTED
        from models.enums import Side as Sd

        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("dlg.import_pcb"),
            qt_settings().value("last_image_dir", ""),
            "PCB/CAD (*.zip *.gbr *.ger *.gtl *.gbl *.gko *.gm1 *.drl *.xln *.kicad_pcb *.dxf *.svg *.brd *.csv *.json);;"
            "Gerber ZIP (*.zip);;KiCad (*.kicad_pcb);;DXF (*.dxf);;SVG (*.svg);;Excellon (*.drl *.xln);;"
            "Eagle BRD (*.brd);;CSV (*.csv);;All Files (*.*)",
        )
        if not path:
            return
        kind = detect_kind(path)
        if kind == "unsupported":
            QMessageBox.warning(self, tr("dlg.import_pcb"), UNSUPPORTED.get(Path(path).suffix.lower(), tr("err.unsupported_format")))
            return
        if Path(path).suffix.lower() == ".csv":
            self._import_csv_points()
            return
        side, ok = QInputDialog.getItem(self, tr("dlg.import_side"), tr("dlg.import_side"), ["B", "A"], 0, False)
        if not ok:
            return
        dest = Sd.A if side == "A" else Sd.B
        try:
            vec = load_pcb(path, dest)
        except Exception as exc:
            QMessageBox.warning(self, tr("dlg.import_pcb"), str(exc))
            return
        snap = self.controller._capture("image_a" if dest == Sd.A else "image_b")
        self.controller.set_vector(dest, vec)
        self.controller._remember("Import PCB/CAD", snap)
        self.refresh_images()
        self.statusBar().showMessage(vec.notes or f"Imported {Path(path).name}")

    def _import_vector(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import vector", qt_settings().value("last_image_dir", ""),
            "Vector (*.svg *.dxf *.csv *.json);;SVG (*.svg);;DXF (*.dxf);;CSV (*.csv);;JSON (*.json);;All Files (*.*)",
        )
        if not path:
            return
        ext = Path(path).suffix.lower()
        if ext == ".svg":
            self._import_svg_path(path)
        elif ext == ".dxf":
            side = self._ask_side()
            if side:
                self.controller.set_vector(side, load_dxf(path, side))
                self.refresh_images()
        elif ext == ".json":
            side = self._ask_side()
            if side:
                self.controller.set_vector(side, load_easyeda(path, side))
                self.refresh_images()
        elif ext == ".csv":
            self._import_csv_contour()

    def _import_svg_path(self, path: str) -> None:
        side = self._ask_side()
        if side:
            self.controller.set_vector(side, load_svg(path, side))
            self.refresh_images()

    def _ask_side(self) -> Side | None:
        side, ok = QInputDialog.getItem(self, "Import side", "Load onto:", ["B", "A"], 0, False)
        if not ok:
            return None
        return Side.A if side == "A" else Side.B

    def _export_contours(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export contours CSV", "contours.csv", "CSV (*.csv)")
        if path:
            try:
                export_contours(path, self.controller.data, self.controller.data.display_unit)
            except ValueError as exc:
                QMessageBox.warning(self, "Export", str(exc))

    def _accept_only(self) -> None:
        self.controller.accept_transform()
        self.refresh_all()

    def _accept_alignment(self) -> None:
        from ui.accept_preview import AcceptPreviewDialog

        if AcceptPreviewDialog(self.controller, self).exec():
            self.refresh_all()

    def mirror_image(self, side: Side, axis: str) -> None:
        if self.controller.is_locked() and self.controller.image(side):
            if QMessageBox.question(
                self,
                "Mirror",
                f"Alignment is LOCKED.\n\nYes = DISPLAY mirror only (view, registration unchanged)\n"
                f"No = cancel\n\nUnlock first to change REGISTRATION orientation of {side.value}.",
            ) != QMessageBox.StandardButton.Yes:
                return
            self.controller.toggle_display_mirror(side, axis=axis)
        else:
            self.controller.toggle_image_mirror(side, axis=axis)
        self.refresh_all()

    def display_mirror(self, side: Side, axis: str) -> None:
        self.controller.toggle_display_mirror(side, axis=axis)
        self.refresh_all()

    def reset_orient(self, side: Side) -> None:
        self.controller.reset_orientation(side)
        self.refresh_all()

    def _calculate(self) -> None:
        rec = self.controller.data.current_transform
        if rec is None:
            QMessageBox.warning(self, "CALCULATE", "Accept registration first.")
            return
        result = self.controller.calculate_comparison()
        if not result:
            return
        self.refresh_overlays()
        self.refresh_chrome()
        from app.i18n import tr

        header = result.get("header") or {}
        physical = bool(header.get("physical_available"))
        u = result.get("unit", "px")
        if physical:
            body = (
                f"{tr('calc.confirmed')}:  {result.get('confirmed')}\n"
                f"{tr('calc.matched')}:    {result.get('matched')}\n"
                f"{tr('calc.mismatched')}: {result.get('mismatched')}\n"
                f"{tr('calc.match_rate')}: {_fmt(result.get('point_match_rate'))}%\n\n"
                f"{tr('calc.mean')}: {_fmt(result.get('mean_error'))} {u}\n"
                f"RMSE: {_fmt(result.get('rmse'))} {u}\n"
                f"{tr('calc.max')}: {_fmt(result.get('max_error'))} {u}\n\n"
                f"{tr('calc.tolerance')}: {result.get('tolerance_label')}\n"
            )
        else:
            body = (
                f"{tr('calc.confirmed')}:  {result.get('confirmed')}\n\n"
                f"{tr('calc.geometric')}:\n"
                f"  {tr('calc.mean')}: {_fmt(result.get('mean_error'))} px\n"
                f"  RMSE: {_fmt(result.get('rmse'))} px\n"
                f"  {tr('calc.max')}: {_fmt(result.get('max_error'))} px\n\n"
                f"{tr('calc.physical')}: {tr('calc.physical_na')}\n"
                f"{tr('calc.physical_reason')}\n"
            )
            self.statusBar().showMessage(tr("calc.physical_status"))
        if not _offscreen():
            QMessageBox.information(self, tr("calc.result_title"), body)

    def _show_log(self) -> None:
        if not self.controller.data.analysis_result:
            self._calculate()
            if not self.controller.data.analysis_result:
                return
        from ui.log_window import LogWindow

        LogWindow(self.controller, self).exec()

    def _set_tolerance(self) -> None:
        from ui.tolerance_dialog import ToleranceDialog

        dlg = ToleranceDialog(self.controller, self)
        result = dlg.exec()
        if result == 2:
            self._calibrate_side(Side.B if self.controller.image(Side.B) else Side.A)
            return
        self.refresh_overlays()
        self.refresh_chrome()

    def _toolbar_tolerance(self) -> None:
        if getattr(self, "_tol_syncing", False):
            return
        unit = Unit(self.toolbar.tol_unit.currentText())
        old_unit = self.controller.data.tolerances.unit
        if unit != old_unit:
            converted = self.controller.convert_tolerance_value(self.controller.data.tolerances.match, old_unit, unit)
            val = float(self.controller.data.tolerances.match if converted is None else converted)
            self._tol_syncing = True
            try:
                self.toolbar.tol_value.setValue(val)
            finally:
                self._tol_syncing = False
        else:
            val = float(self.toolbar.tol_value.value())
        self.controller.set_compare_tolerance(val, unit)
        if unit != Unit.PX and not self.controller.physical_calibration_ready():
            self._physical_scale_status()
            self.refresh_overlays()
            self.refresh_chrome()
            return
        self.refresh_overlays()
        self.refresh_chrome()

    def _sync_toolbar_tolerance(self) -> None:
        if not hasattr(self, "toolbar") or not hasattr(self.toolbar, "tol_value"):
            return
        self._tol_syncing = True
        try:
            t = self.controller.data.tolerances
            if abs(self.toolbar.tol_value.value() - t.match) > 1e-9:
                self.toolbar.tol_value.setValue(t.match)
            if self.toolbar.tol_unit.currentText() != t.unit.value:
                self.toolbar.tol_unit.setCurrentText(t.unit.value)
        finally:
            self._tol_syncing = False

    def _physical_scale_status(self) -> None:
        from app.i18n import tr

        self.statusBar().showMessage(tr("status.scale_not_set"))

    def _physical_tol_blocked(self) -> None:
        box = QMessageBox(self)
        from app.i18n import tr

        box.setWindowTitle(tr("status.scale_not_set"))
        box.setText(tr("dlg.mm_tol_text"))
        box.setInformativeText(tr("dlg.mm_tol_info"))
        set_btn = box.addButton(tr("btn.set_scale"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(tr("crop.cancel"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() == set_btn:
            self._start_set_scale()

    def _start_set_scale(self, side: Side | None = None) -> None:
        if side is None:
            side = Side.B if self.controller.image(Side.B) else Side.A
        if self.controller.image(side) is None:
            QMessageBox.warning(self, "SET SCALE", "Load an image first.")
            return
        img = self.controller.image(side)
        img.derivation["_cal_pts"] = []
        self.set_tool(ActiveTool.CALIBRATION)
        self.statusBar().showMessage(f"SET SCALE {side.value}: click two ends of a known distance.")

    def _on_scale_clicks_ready(self, side) -> None:
        from app.i18n import tr

        img = self.controller.image(side)
        pts = (img.derivation.get("_cal_pts") or []) if img else []
        if len(pts) < 2:
            return
        import math

        p0, p1 = pts[-2], pts[-1]
        pix = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        if pix <= 0 or not math.isfinite(pix):
            QMessageBox.warning(self, tr("tb.set_scale"), tr("calc.scale_invalid"))
            return
        dlg = KnownDistanceDialog(pix, self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        known = dlg.known_mm()
        if known <= 0 or not math.isfinite(known):
            QMessageBox.warning(self, tr("tb.set_scale"), tr("calc.scale_invalid"))
            return
        trial = known / pix
        if trial < 0.002 or trial > 1.0:
            if QMessageBox.question(
                self,
                tr("tb.set_scale"),
                tr("calc.scale_unusual"),
            ) != QMessageBox.StandardButton.Yes:
                return
        if self.controller.finish_scale_mm(side, known):
            cal = self.controller.image(side).calibration
            self.controller.data.display_unit = Unit.MM
            if self.controller.data.tolerances.unit == Unit.PX:
                converted = self.controller.convert_tolerance_value(
                    self.controller.data.tolerances.match, Unit.PX, Unit.MM
                )
                if converted is not None:
                    self.controller.set_compare_tolerance(converted, Unit.MM)
            self.statusBar().showMessage(
                f"Scale {side.value}: {cal.mm_per_px:.6f} mm/px  ({pix:.3f} px = {dlg.known_mm():g} mm)"
            )
            self.set_tool(ActiveTool.PAN)
            self.refresh_chrome()

    def _sync_scale_status(self) -> None:
        if not hasattr(self.toolbar, "scale_status"):
            return
        img = self.controller.image(Side.B) or self.controller.image(Side.A)
        other = self.controller.image(Side.A if img is self.controller.image(Side.B) else Side.B)
        from app.i18n import tr

        def _side_label(doc, name: str) -> str:
            if doc is None:
                return tr("tb.scale_side_none", side=name)
            if not doc.calibration.is_calibrated:
                return tr("tb.scale_side_off", side=name)
            return tr("tb.scale_side_on", side=name)

        a = self.controller.image(Side.A)
        b = self.controller.image(Side.B)
        parts = [_side_label(a, "A"), _side_label(b, "B")]
        if a is None and b is None:
            self.toolbar.scale_status.setText(tr("tb.scale_not_set"))
        else:
            self.toolbar.scale_status.setText("  ".join(parts))
        tip = []
        for name, doc in (("A", a), ("B", b)):
            if doc is None:
                continue
            if doc.calibration.is_calibrated:
                extra = " derived" if doc.calibration.derived else ""
                tip.append(f"{name}: {doc.calibration.mm_per_px:.6f} mm/px{extra}")
            else:
                tip.append(f"{name}: NOT SET")
        self.toolbar.scale_status.setToolTip("\n".join(tip) or tr("tip.scale_status"))
        _ = other

    def _export_annotated(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export annotated image",
            "annotated.png",
            "PNG (*.png);;JPEG (*.jpg *.jpeg)",
        )
        if not path:
            return
        mode, ok = QInputDialog.getItem(
            self,
            "Export resolution",
            "Annotations are drawn in original image coordinates.",
            ["Original Resolution", "Current View", "Custom Resolution"],
            0,
            False,
        )
        if not ok:
            return
        side, ok = QInputDialog.getItem(self, "Side", "Image:", ["A", "B"], 0, False)
        if not ok:
            return
        s = Side.A if side == "A" else Side.B
        rgb = array_to_rgb(self.controller.view_array(s) if self.controller.view_array(s) is not None else np.zeros((1, 1, 3), np.uint8))
        if self.controller.view_array(s) is None:
            QMessageBox.warning(self, "Export", f"Image {side} is not loaded.")
            return
        src_h, src_w = rgb.shape[:2]
        if mode == "Current View":
            from exporters.image_export import scale_rgb

            view = self.view_a if s == Side.A else self.view_b
            vw = max(1, view.viewport().width())
            rgb = scale_rgb(rgb, vw / max(1, src_w))
        elif mode == "Custom Resolution":
            w, ok = QInputDialog.getInt(self, "Width", "Output width (px):", src_w, 64, 20000)
            if not ok:
                return
            scale = w / max(1, src_w)
            from exporters.image_export import scale_rgb

            rgb = scale_rgb(rgb, scale)
        pts = []
        for lm in self.controller.data.landmarks:
            xy = lm.coord(s)
            if xy is None:
                continue
            wx, wy = self.controller._work_xy(s, xy[0], xy[1])
            vx, vy = self.controller.view_xy(s, wx, wy)
            if mode in ("Custom Resolution", "Current View"):
                vx *= rgb.shape[1] / src_w
                vy *= rgb.shape[0] / src_h
            pts.append((vx, vy, lm.label or lm.id, (39, 174, 96)))
        quality = self._jpeg_quality_if_needed(path)
        ow, oh = export_annotated(path, rgb, pts, jpeg_quality=quality)
        self.statusBar().showMessage(f"Exported annotated {side} at {ow}×{oh} (source {src_w}×{src_h})")

    def _jpeg_quality_if_needed(self, path: str) -> int | None:
        if Path(path).suffix.lower() not in {".jpg", ".jpeg"}:
            return None
        q, ok = QInputDialog.getInt(self, "JPEG quality", "Quality (engineering screenshots prefer PNG):", 95, 1, 100)
        return q if ok else 95

    def _image_info(self) -> None:
        from ui.image_info import ImageInfoDialog

        ImageInfoDialog(self.controller, self).exec()

    def _set_pixel_precise(self, on: bool) -> None:
        self.view_a.set_pixel_precise(on)
        self.view_b.set_pixel_precise(on)
        if hasattr(self, "act_smooth"):
            self.act_smooth.setChecked(not on)
            self.act_pixel.setChecked(on)

    def _undo_history(self) -> None:
        from ui.undo_history import UndoHistoryDialog

        UndoHistoryDialog(self.controller.undo_stack, self).exec()

    def _reset_alignment(self) -> None:
        if QMessageBox.question(self, "Reset alignment", "Remove ZERO, SCALE, and the accepted transform?") != QMessageBox.StandardButton.Yes:
            return
        also = QMessageBox.question(
            self,
            "CONTROL data",
            "Also delete CONTROL / measurement landmarks?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        self.controller.reset_alignment(delete_controls=also == QMessageBox.StandardButton.Yes)
        self.refresh_all()

    def _remove_image(self, side: Side) -> None:
        if self.controller.image(side) is None:
            return
        clear = False
        if self.controller.data.landmarks:
            ans = QMessageBox.question(
                self,
                "Remove image",
                f"Remove MODEL {side.value}?\n\nYes = keep measurement data\nNo = also clear points on this side",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            )
            if ans == QMessageBox.StandardButton.Cancel:
                return
            clear = ans == QMessageBox.StandardButton.No
        self.controller.clear_image(side, clear_side_points=clear)
        if side == Side.A:
            self._last_a_shape = None
        else:
            self._last_b_shape = None
        self.refresh_all()

    def _build_mesh(self, group: str | None = None) -> None:
        self.controller.build_mesh(group=group)
        self.controller.data.mesh.view_mode = MeshViewMode.GHOST.value
        self.refresh_overlays()

    def _toggle_mesh(self) -> None:
        mesh = self.controller.data.mesh
        if mesh.view_mode != MeshViewMode.HIDDEN.value:
            if mesh.view_mode != MeshViewMode.SKELETON.value:
                try:
                    self._last_mesh_mode = MeshViewMode(mesh.view_mode)
                except ValueError:
                    self._last_mesh_mode = MeshViewMode.GHOST
            mesh.view_mode = MeshViewMode.HIDDEN.value
        else:
            mesh.view_mode = (self._last_mesh_mode or MeshViewMode.GHOST).value
        self.refresh_overlays()
        self._refresh_mode_ui()
        self._sync_vis_actions()

    def _set_mesh_mode(self, mode: MeshViewMode) -> None:
        if mode == MeshViewMode.SKELETON and self.controller.data.mesh.view_mode == MeshViewMode.SKELETON.value:
            self._toggle_skeleton()
            return
        if mode != MeshViewMode.HIDDEN and mode != MeshViewMode.SKELETON:
            self._last_mesh_mode = mode
        self.controller.data.mesh.view_mode = mode.value
        if mode == MeshViewMode.SKELETON:
            self.controller.data.mesh.dim_images = True
        elif mode != MeshViewMode.HIDDEN:
            self.controller.data.mesh.dim_images = False
        self.refresh_images()
        self._refresh_mode_ui()

    def _set_vis(self, attr: str, value: bool) -> None:
        if hasattr(self.controller.data.mesh, attr):
            setattr(self.controller.data.mesh, attr, value)
        elif hasattr(self.controller.data, attr):
            setattr(self.controller.data, attr, value)
        self._sync_vis_actions()
        self.refresh_overlays()

    def _toggle_mesh_flag(self, attr: str) -> None:
        mesh = self.controller.data.mesh
        setattr(mesh, attr, not bool(getattr(mesh, attr)))
        self._sync_vis_actions()
        self.refresh_overlays()

    def _toggle_data_flag(self, attr: str) -> None:
        setattr(self.controller.data, attr, not bool(getattr(self.controller.data, attr)))
        self._sync_vis_actions()
        self.refresh_overlays()

    def _sync_vis_actions(self) -> None:
        d = self.controller.data
        mesh = d.mesh
        pairs = (
            ("act_vis_zero", d.show_zero_scale),
            ("act_show_points", mesh.show_points),
            ("act_show_ids", mesh.show_ids),
            ("act_vis_expected", mesh.show_expected),
            ("act_vis_actual", mesh.show_actual),
            ("act_vis_vectors", mesh.show_vectors),
            ("act_vis_mesh", mesh.view_mode != MeshViewMode.HIDDEN.value),
            ("act_vis_dims", d.show_dimensions),
            ("act_vis_contours", d.show_contours),
            ("act_vis_meas", d.show_measurements),
        )
        for name, val in pairs:
            act = getattr(self, name, None)
            if act is not None:
                act.blockSignals(True)
                act.setChecked(bool(val))
                act.blockSignals(False)
        if hasattr(self, "layers") and hasattr(self.layers, "sync_from_project"):
            self.layers.sync_from_project()

    def _mesh_edit(self, mode: str) -> None:
        self.controller.mesh_edit = mode
        self.controller._mesh_pick = None
        self.statusBar().showMessage(f"Mesh {mode}: click two CONTROL landmarks.")

    def _mesh_click(self, side: Side, x: float, y: float) -> None:
        lm = self.controller.nearest_landmark(side, x, y, radius=28)
        if lm is None:
            self.statusBar().showMessage("Click a CONTROL landmark.")
            return
        if self.controller._mesh_pick is None:
            self.controller._mesh_pick = lm.id
            self.statusBar().showMessage(f"Selected {lm.id} — click the other landmark.")
            return
        a, b = self.controller._mesh_pick, lm.id
        mode = self.controller.mesh_edit
        if mode == "add":
            self.controller.add_mesh_edge(a, b)
        elif mode == "remove":
            self.controller.remove_mesh_edge(a, b)
        elif mode == "lock":
            self.controller.lock_mesh_edge(a, b)
        self.controller.mesh_edit = None
        self.controller._mesh_pick = None
        self.refresh_overlays()

    def _draw_mesh(self) -> None:
        mesh = self.controller.data.mesh
        mode = mesh.view_mode
        skeleton = mode == MeshViewMode.SKELETON.value
        self.view_a.set_image_opacity(0.18 if mesh.dim_images or skeleton else 1.0)
        self.view_b.set_image_opacity(0.18 if mesh.dim_images or skeleton else 1.0)
        if mode in (MeshViewMode.HIDDEN.value, MeshViewMode.POINTS_ONLY.value) or not mesh.edges:
            return
        pts_a = self.controller.mesh_points(Side.A)
        pts_b_act = self.controller.mesh_points(Side.B)
        pts_b_exp = self.controller.mesh_points(Side.B, predicted=True)
        show_expected = mesh.show_expected and mode in (
            MeshViewMode.MESH.value,
            MeshViewMode.MESH_VECTORS.value,
            MeshViewMode.EXPECTED.value,
            MeshViewMode.BOTH.value,
            MeshViewMode.GHOST.value,
            MeshViewMode.ERROR_MAP.value,
            MeshViewMode.LOCAL_SCALE.value,
            MeshViewMode.LOCAL_ROTATION.value,
            MeshViewMode.STRETCH.value,
            MeshViewMode.SKELETON.value,
        )
        show_actual = mesh.show_actual and mode in (
            MeshViewMode.MESH.value,
            MeshViewMode.MESH_VECTORS.value,
            MeshViewMode.ACTUAL.value,
            MeshViewMode.BOTH.value,
            MeshViewMode.GHOST.value,
            MeshViewMode.ERROR_MAP.value,
            MeshViewMode.LOCAL_SCALE.value,
            MeshViewMode.LOCAL_ROTATION.value,
            MeshViewMode.STRETCH.value,
            MeshViewMode.SKELETON.value,
        )
        tri_rows = {tuple(sorted((r["P1"], r["P2"], r["P3"]))): r for r in self.controller.mesh_triangle_rows()}
        for e in mesh.edges:
            if getattr(e, "excluded", False):
                continue
            payload = f"edge:{e.a}:{e.b}"
            esel = any(s[0] == "mesh_edge" and {s[1], s[2]} == {e.a, e.b} for s in self._selection)
            if e.a in pts_a and e.b in pts_a:
                pa, pb = pts_a[e.a], pts_a[e.b]
                self.view_a.add_mesh_segment(pa[0], pa[1], pb[0], pb[1], "mesh_actual", width=1.6, payload=payload, selected=esel)
            if show_expected and e.a in pts_b_exp and e.b in pts_b_exp:
                pa, pb = pts_b_exp[e.a], pts_b_exp[e.b]
                self.view_b.add_mesh_segment(
                    pa[0], pa[1], pb[0], pb[1], "mesh_expected", width=1.0, alpha=110, dashed=True, payload=payload, selected=esel
                )
            if show_actual and e.a in pts_b_act and e.b in pts_b_act:
                pa, pb = pts_b_act[e.a], pts_b_act[e.b]
                color = "mesh_actual"
                width = 2.0
                if mode in (MeshViewMode.STRETCH.value, MeshViewMode.ERROR_MAP.value, MeshViewMode.LOCAL_SCALE.value):
                    row = next((r for r in self.controller.mesh_edge_rows() if {r["POINT_A"], r["POINT_B"]} == {e.a, e.b}), None)
                    if row and row.get("PERCENT_DELTA") is not None:
                        pct = float(row["PERCENT_DELTA"])
                        color = "residual" if abs(pct) > 1.5 else "mesh_actual"
                        width = 1.4 + min(3.0, abs(pct) / 2.0)
                self.view_b.add_mesh_segment(pa[0], pa[1], pb[0], pb[1], color, width=width, payload=payload, selected=esel)
        if mode in (MeshViewMode.LOCAL_SCALE.value, MeshViewMode.LOCAL_ROTATION.value) and tri_rows:
            self.statusBar().showMessage(
                "Triangle diagnostics: click an edge for lengths, or inspect Statistics after export."
            )

    def _select_landmark(self, pid: str) -> None:
        self.show_dock("points")
        self.point_table.select_items([("landmark", pid, None)])

    def _show_mesh_item(self, pid: str) -> None:
        rows = [r for r in self.controller.mesh_edge_rows() if pid in (r["POINT_A"], r["POINT_B"])]
        if not rows:
            return
        lines = []
        for r in rows[:8]:
            lines.append(
                f"{r['POINT_A']}–{r['POINT_B']}: A={r['LENGTH_A']}  B={r['LENGTH_B']}  Δ={r['DELTA']}  ({r['PERCENT_DELTA']}%)"
            )
        self.statusBar().showMessage("  |  ".join(lines))

    def _export_mesh_edges(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export mesh edges", "mesh_edges.csv", "CSV (*.csv)")
        if not path:
            return
        from exporters.csv_export import export_rows

        rows = self.controller.mesh_edge_rows()
        if not rows:
            QMessageBox.warning(self, "Export", "No mesh edges to export. Build Mesh first.")
            return
        export_rows(path, list(rows[0].keys()), rows, metadata={"content": "mesh_edges"})

    def _export_mesh_triangles(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export triangles", "mesh_triangles.csv", "CSV (*.csv)")
        if not path:
            return
        from exporters.csv_export import export_rows

        rows = self.controller.mesh_triangle_rows()
        if not rows:
            QMessageBox.warning(self, "Export", "No triangles to export. Build Mesh first.")
            return
        export_rows(path, list(rows[0].keys()), rows, metadata={"content": "mesh_triangles"})

    def _export_geometry_map(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export geometry map", "geometry_map.png", "PNG (*.png)")
        if not path:
            return
        pix = self.view_a.grab()
        pix.save(path)

    def _toggle_advanced(self, checked: bool = False) -> None:
        self.controller.advanced_mode = bool(checked) if isinstance(checked, bool) else not self.controller.advanced_mode
        self.act_advanced.setChecked(self.controller.advanced_mode)
        if self.controller.advanced_mode:
            for key in ("points", "transform", "statistics"):
                self.show_dock(key)
        self.statusBar().showMessage("Advanced Mode ON" if self.controller.advanced_mode else "Basic Mode")

    def _maybe_first_run(self) -> None:
        import os

        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        settings = qt_settings()
        if settings.value("first_run_guide_v2", False):
            return
        self._first_run_guide()
        settings.setValue("first_run_guide_v2", True)

    def _first_run_guide(self) -> None:
        QMessageBox.information(
            self,
            "HOW TO START",
            "1. Load two images.\n"
            "2. Click the same ZERO point on both.\n"
            "3. Click the same SCALE point on both.\n"
            "4. Accept and lock alignment.\n"
            "5. Add CONTROL landmarks.\n"
            "6. Build Mesh.\n"
            "7. Verify corresponding points.\n"
            "8. Inspect errors.\n\n"
            "ZERO and SCALE register the images.\n"
            "CONTROL points measure — they never change registration.",
        )

    def _set_orient(self, name: OrientationName) -> None:
        self.controller.apply_orientation_b(name)
        self.refresh_all()
        self._refresh_orient_buttons()

    def _set_orient_side(self, side: Side, name: OrientationName) -> None:
        from core.orientation import Orientation

        self.controller.apply_orientation(side, Orientation.from_name(name))
        self.refresh_all()
        self._refresh_orient_buttons()

    def _refresh_orient_buttons(self) -> None:
        from app.i18n import tr

        if not hasattr(self, "toolbar"):
            return
        for side, attr in ((Side.A, "mirror_a_btn"), (Side.B, "mirror_b_btn")):
            btn = getattr(self.toolbar, attr, None)
            if btn is None:
                continue
            img = self.controller.image(side)
            label = tr("or.normal")
            extra = ""
            if img is not None:
                names = {
                    OrientationName.NORMAL: tr("or.normal"),
                    OrientationName.MIRROR_X: tr("or.mirror_x"),
                    OrientationName.MIRROR_Y: tr("or.mirror_y"),
                    OrientationName.ROTATE_90: tr("or.rot90"),
                    OrientationName.ROTATE_180: tr("or.rot180"),
                    OrientationName.ROTATE_270: tr("or.rot270"),
                }
                label = names.get(img.orientation.name, img.orientation.status_label())
                bits = []
                if img.view_flip_x:
                    bits.append("DISP X")
                if img.view_flip_y:
                    bits.append("DISP Y")
                extra = (" " + "+".join(bits)) if bits else ""
            prefix = tr("tb.orient_a") if side == Side.A else tr("tb.orient_b")
            btn.setText(f"{prefix}: {label}{extra}")

    def _calibrate_side(self, side: Side) -> None:
        self._start_set_scale(side)

    def _front_back_dialog(self) -> None:
        items = ["A FRONT / B FRONT", "A FRONT / B BACK", "A BACK / B FRONT", "A BACK / B BACK"]
        cur, ok = QInputDialog.getItem(self, "Front / Back", "Sides (BACK is never mirrored implicitly):", items, 0, False)
        if not ok:
            return
        mapping = {
            items[0]: (PcbSide.FRONT, PcbSide.FRONT),
            items[1]: (PcbSide.FRONT, PcbSide.BACK),
            items[2]: (PcbSide.BACK, PcbSide.FRONT),
            items[3]: (PcbSide.BACK, PcbSide.BACK),
        }
        sa, sb = mapping[cur]
        self._set_pcb_side(Side.A, sa)
        self._set_pcb_side(Side.B, sb)

    def _select_all_points(self) -> None:
        items = [("landmark", lm.id, None) for lm in self.controller.data.landmarks if getattr(lm, "visible", True)]
        self._set_selection(items)
        self.show_dock("points")
        self.point_table.table.selectAll()

    def _toggle_precision_points(self) -> None:
        self.controller.data.precision_points = bool(self.act_precision_points.isChecked())
        self._apply_appearance()
        self.refresh_overlays()

    def _clear_kind(self, kind: str) -> None:
        from app.i18n import tr

        titles = {
            "control": tr("sel.clear_controls"),
            "dimensions": tr("sel.clear_dims"),
            "contours": tr("sel.clear_contours"),
            "measurements": tr("sel.clear_meas"),
        }
        if not _offscreen():
            if QMessageBox.question(self, titles.get(kind, tr("sel.delete")), tr("sel.confirm_clear")) != QMessageBox.StandardButton.Yes:
                return
        if kind == "control":
            self.controller.clear_landmarks_of_types((PointType.CONTROL,))
        elif kind == "dimensions":
            self.controller.clear_dimensions()
        elif kind == "contours":
            self.controller.clear_contours()
        elif kind == "measurements":
            self.controller.clear_measurements()
        self._selection = []
        self.refresh_overlays()
        self.refresh_chrome()

    def _properties_selected(self) -> None:
        from app.i18n import tr

        if not self._selection:
            return
        lines = []
        for s in self._selection:
            lines.append(self._hit_label(s))
            if s[0] == "landmark":
                lm = next((x for x in self.controller.data.landmarks if x.id == s[1]), None)
                if lm:
                    lines.append(f"  type={lm.point_type.value}  A=({lm.a_x},{lm.a_y})  B=({lm.b_x},{lm.b_y})  {lm.confidence.value}")
        QMessageBox.information(self, tr("sel.properties"), "\n".join(lines) or "—")

    def _hit_label(self, item: tuple) -> str:
        kind = item[0]
        if kind == "landmark":
            return str(item[1])
        if kind == "contour":
            return f"Contour {item[1]}"
        if kind == "contour_node":
            return f"Contour {item[1]} node {item[2]}"
        if kind == "dimension":
            return f"Dimension {item[1]}"
        if kind == "mesh_edge":
            return f"Mesh Edge {item[1]}-{item[2]}"
        return str(item)

    def _augment_view_menu(self, side: Side, menu, sx: float, sy: float) -> None:
        from app.i18n import tr

        wx, wy = self.controller.unview_xy(side, sx, sy)
        hits = self._hit_objects(side, wx, wy)
        if hits and not self._selection:
            self._set_selection([hits[0]])
        if self._selection:
            menu.addAction(tr("sel.delete")).triggered.connect(self._delete_selected)
            menu.addAction(tr("sel.hide")).triggered.connect(self._hide_selected)
            menu.addAction(tr("sel.properties")).triggered.connect(self._properties_selected)
            if any(s[0] == "landmark" for s in self._selection):
                menu.addAction(tr("sel.reset_actual")).triggered.connect(self._reset_actual_selected)
                menu.addAction(tr("sel.delete_actual")).triggered.connect(self._reset_actual_selected)
            menu.addSeparator()
        if hits:
            sub = menu.addMenu(tr("sel.select_object"))
            for h in hits:
                sub.addAction(self._hit_label(h)).triggered.connect(lambda checked=False, it=h: self._set_selection([it]))
            menu.addSeparator()

    def _center_id(self, pid: str, side: Side) -> None:
        self._center_item(("landmark", pid, None), sides=(side,))

    def _reset_actual_id(self, pid: str) -> None:
        self.controller.reset_actual(pid)
        self.refresh_overlays()
        self.refresh_chrome()

    def _hide_ids(self, pids: list) -> None:
        items = [("landmark", pid, None) for pid in pids]
        self.controller.set_objects_visible(items, False)
        self.refresh_overlays()

    def _manager_select(self, items: list) -> None:
        if self._syncing_manager:
            return
        if items and isinstance(items[0], str):
            items = [("landmark", pid, None) for pid in items]
        self._syncing_manager = True
        try:
            self._set_selection(items)
        finally:
            self._syncing_manager = False

    def _show_object(self, item) -> None:
        if isinstance(item, str):
            item = ("landmark", item, None)
        if item[0] == "landmark":
            self.controller.set_landmark_visible(item[1], True)
        else:
            self.controller.set_objects_visible([item], True)
        self.refresh_overlays()

    def _preferences(self) -> None:
        from ui.preferences_dialog import PreferencesDialog

        if PreferencesDialog(self.controller, self).exec():
            self._set_mag(self.controller.magnifier_factor)
            self._apply_appearance()
            if hasattr(self, "act_precision"):
                self.act_precision.setChecked(self.controller.data.precision_drawing)
            self.refresh_overlays()
            self.refresh_chrome()

    def _apply_appearance(self) -> None:
        d = self.controller.data
        for view in (self.view_a, self.view_b):
            view.set_appearance(
                line=d.contour_line_width,
                node=d.contour_node_px,
                selected=d.contour_selected_node_px,
                handle=d.contour_handle_px,
                precision=d.precision_drawing,
                magnifier_on=d.magnifier_on,
                magnifier_original=d.magnifier_from_original,
                marker=d.point_marker_px,
                cross=d.center_cross_px,
                precision_points=d.precision_points,
                label=d.point_label_px,
            )
            view.magnifier.set_factor(self.controller.magnifier_factor)

    def _toggle_precision(self) -> None:
        self.controller.data.precision_drawing = bool(self.act_precision.isChecked())
        self._apply_appearance()
        self.refresh_overlays()

    def _toggle_magnifier(self) -> None:
        on = bool(self.act_magnifier.isChecked())
        if on:
            self._set_magnifier_mode(int(self.controller.magnifier_factor or 10))
        else:
            self._set_magnifier_mode(0)

    def _clear_mesh_confirm(self) -> None:
        if not self.controller.data.mesh.edges:
            return
        if not _offscreen():
            if QMessageBox.question(
                self,
                "Clear Mesh",
                "Delete mesh topology? This does not delete CONTROL points.",
            ) != QMessageBox.StandardButton.Yes:
                return
        self.controller.clear_mesh()
        self.refresh_overlays()
        self._refresh_mode_ui()

    def _enhance_caption(self, adj) -> str:
        from app.i18n import tr

        if int(getattr(adj, "super_res_factor", 1) or 1) > 1:
            return tr("adj.sr_view")
        return tr("adj.enhanced_view")

    def _show_audit(self) -> None:
        from ui.audit_dialog import AuditDialog

        AuditDialog(self.controller, self).exec()

    def _quick_start(self) -> None:
        QMessageBox.information(
            self,
            "Quick Start",
            "BASIC WORKFLOW\n\n"
            "1. Load A and B.\n"
            "2. Set ZERO.\n"
            "3. Set SCALE.\n"
            "4. Accept and Lock.\n"
            "5. Add CONTROL points.\n"
            "6. Build Mesh.\n"
            "7. Set physical scale if mm required (SET SCALE).\n"
            "8. Set tolerance.\n"
            "9. Calculate.\n"
            "10. Open Log.\n\n"
            "Optional: Crop, Image Adjustments, Mirror, Dimension, Contour, Overlay, Sync.\n"
            "CONTROL / dimensions / contours / mesh never change a locked alignment.",
        )

    def _shortcuts_help(self) -> None:
        QMessageBox.information(
            self,
            "Keyboard shortcuts",
            "Ctrl+O  Open project\nCtrl+S  Save\nCtrl+V  Paste image\n"
            "Ctrl+Z / Ctrl+Y  Undo / Redo\n"
            "R  Reference   M  CONTROL   D  Dimension   Contour from toolbar\n"
            "F  Fit both    1  Actual size A\n"
            "Tab  Image workspace (hide docks)\nF11  Full screen\n"
            "Space  Flicker / temporary pan\nEsc  Pan tool\nDelete  Delete selected point",
        )

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "About PCB Image Comparator",
            "PCB Image Comparator\nEngineering measurement tool.\n"
            "Original files are never overwritten.\n"
            "Measurement points never change a locked alignment.",
        )

    def _rebuild_recent_menu(self) -> None:
        self.recent_menu.clear()
        recent = qt_settings().value("recent_projects") or []
        if isinstance(recent, str):
            recent = [recent]
        if not recent:
            empty = QAction("(none)", self)
            empty.setEnabled(False)
            self.recent_menu.addAction(empty)
            return
        for path in recent[:10]:
            self.recent_menu.addAction(str(path), lambda checked=False, p=path: self._open_recent(p))

    def _open_recent(self, path: str) -> None:
        if not Path(path).is_file():
            QMessageBox.warning(self, "Recent", f"File not found:\n{path}")
            return
        self.controller.load(path)
        self.refresh_all()

    def _remember_project(self, path: Path) -> None:
        settings = qt_settings()
        recent = settings.value("recent_projects") or []
        if isinstance(recent, str):
            recent = [recent]
        text = str(path)
        recent = [text] + [p for p in recent if p != text]
        settings.setValue("recent_projects", recent[:12])
        self._rebuild_recent_menu()

    def connected_action_report(self) -> list[dict]:
        tracked = set(self._all_actions)
        rows = []
        for action in self.findChildren(QAction):
            if action.isSeparator() or not action.text():
                continue
            rows.append(
                {
                    "text": action.text().replace("&", ""),
                    "enabled": action.isEnabled(),
                    "connected": action in tracked or action.menu() is not None or action.isCheckable(),
                    "shortcut": action.shortcut().toString() if action.shortcut() else "",
                    "submenu": action.menu() is not None,
                    "tested": False,
                }
            )
        return rows


class _CalibrationDock(QWidget):
    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        from PySide6.QtWidgets import QPushButton

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Each image has an independent scale."))
        ba = QPushButton("CALIBRATE A")
        bb = QPushButton("CALIBRATE B")
        ba.clicked.connect(lambda: window._calibrate_side(Side.A))
        bb.clicked.connect(lambda: window._calibrate_side(Side.B))
        layout.addWidget(ba)
        layout.addWidget(bb)
        self.info = QLabel("—")
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        layout.addStretch()
        window.controller.changed.connect(self._refresh)
        window.controller.imagesChanged.connect(self._refresh)
        self._window = window
        self._refresh()

    def _refresh(self) -> None:
        lines = []
        for side in (Side.A, Side.B):
            img = self._window.controller.image(side)
            if img is None:
                lines.append(f"{side.value}: not loaded")
            elif img.calibration.is_calibrated:
                extra = " DERIVED FROM A + REGISTRATION" if img.calibration.derived and img.calibration.notes else ""
                lines.append(f"{side.value}: {img.calibration.mm_per_px:.6f} mm/px{extra}")
            else:
                lines.append(f"{side.value}: not calibrated")
        self.info.setText("\n".join(lines))


class _NotesDock(QWidget):
    def __init__(self, controller) -> None:
        super().__init__()
        from PySide6.QtWidgets import QPlainTextEdit, QPushButton

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Project notes"))
        self.notes = QPlainTextEdit()
        self.notes.setPlainText(controller.data.notes)
        layout.addWidget(self.notes, 1)
        save = QPushButton("Save notes")
        save.clicked.connect(self._save)
        layout.addWidget(save)
        self.audit = QLabel("")
        self.audit.setWordWrap(True)
        layout.addWidget(self.audit)
        self.controller = controller
        self._refresh_audit()
        controller.changed.connect(self._refresh_audit)

    def _save(self) -> None:
        self.controller.data.notes = self.notes.toPlainText()
        self.controller.mark_dirty()

    def _refresh_audit(self) -> None:
        last = self.controller.data.audit[-8:]
        self.audit.setText("\n".join(f"{e.action}: {e.detail}" for e in last) or "(no audit yet)")


def _overlay_qtransform(controller, *, base: Side | None = None):
    """Place the other image into `base` space using the accepted A→B transform.

    B over A (base=A): B-view pixels → A-view via T⁻¹.
    A over B (base=B): A-view pixels → B-view via T.
    """
    from PySide6.QtGui import QTransform

    xf = controller.accepted_transform()
    if xf is None:
        return QTransform()
    if base is None:
        base = Side.A

    def flip(doc):
        m = np.eye(3)
        if doc is None:
            return m
        w, h = doc.oriented_size()
        if doc.view_flip_x:
            m[0, 0] = -1.0
            m[0, 2] = w - 1.0
        if doc.view_flip_y:
            m[1, 1] = -1.0
            m[1, 2] = h - 1.0
        return m

    fa, fb = flip(controller.image(Side.A)), flip(controller.image(Side.B))
    if base == Side.B:
        m = fb @ xf.matrix() @ fa
    else:
        m = fa @ xf.inverse().matrix() @ fb
    return QTransform(float(m[0, 0]), float(m[1, 0]), float(m[0, 1]), float(m[1, 1]), float(m[0, 2]), float(m[1, 2]))


def _fmt(v) -> str:
    if v is None:
        return "—"
    return f"{float(v):.2f}"


