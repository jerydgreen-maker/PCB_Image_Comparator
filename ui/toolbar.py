"""Two permanently visible toolbar rows. No QToolBar overflow >>."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.i18n import tr
from models.enums import ActiveTool, OrientationName, OverlayMode, Side
from ui.decimal_spin import DualDecimalSpinBox


class MainToolBar(QToolBar):
    """Single QToolBar hosting two permanent rows so Qt never hides actions behind >>."""

    def __init__(self, window) -> None:
        super().__init__("Main", window)
        self.window = window
        self.setMovable(False)
        self.setFloatable(False)
        self.setMinimumHeight(68)
        self.setAllowedAreas(Qt.ToolBarArea.TopToolBarArea)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setStyleSheet(
            "QToolBar { border: none; spacing: 0px; padding: 0px; }"
            "QToolBarExtension { width: 0px; max-width: 0px; min-width: 0px; }"
            "QToolButton { padding: 2px 5px; margin: 0px; }"
        )

        host = QWidget()
        host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        v = QVBoxLayout(host)
        v.setContentsMargins(4, 2, 4, 2)
        v.setSpacing(2)
        self.row1 = QHBoxLayout()
        self.row2 = QHBoxLayout()
        self.row1.setSpacing(3)
        self.row2.setSpacing(3)
        self.row1.setContentsMargins(0, 0, 0, 0)
        self.row2.setContentsMargins(0, 0, 0, 0)
        v.addLayout(self.row1)
        v.addLayout(self.row2)
        self.addWidget(host)

        self._buttons: dict[str, QToolButton] = {}
        self.actions_by_tool: dict[ActiveTool, QAction] = {}
        self._tool_group = QActionGroup(self)
        self._tool_group.setExclusive(False)

        # ROW 1 — project / registration
        self._btn(self.row1, "open_a", tr("tb.open_a"), lambda: window._load_image(Side.A), tr("tip.open_a"))
        self._btn(self.row1, "open_b", tr("tb.open_b"), lambda: window._load_image(Side.B), tr("tip.open_b"))
        self._btn(self.row1, "paste_a", tr("tb.paste_a"), lambda: window.paste_image(Side.A), tr("tip.paste_a"))
        self._btn(self.row1, "paste_b", tr("tb.paste_b"), lambda: window.paste_image(Side.B), tr("tip.paste_b"))
        self._sep(self.row1)
        self._tool(self.row1, ActiveTool.ZERO, tr("tb.zero"), "Z", tr("tip.zero"))
        self._tool(self.row1, ActiveTool.SCALE, tr("tb.scale"), "S", tr("tip.scale"))
        self._tool(self.row1, ActiveTool.CONTROL, tr("tb.control"), "M", tr("tip.control"))
        self._sep(self.row1)
        self.mirror_a_btn = self._orient_btn(self.row1, Side.A, "mirror_a")
        self.mirror_b_btn = self._orient_btn(self.row1, Side.B, "mirror_b")
        self._sep(self.row1)
        self._btn(self.row1, "accept", tr("tb.accept"), window._accept_alignment, tr("tip.accept"))
        self.lock_btn = self._btn(self.row1, "lock", tr("tb.lock"), window.toggle_lock, tr("tip.lock"))
        self.lock = self.lock_btn.defaultAction() if self.lock_btn.defaultAction() else QAction(tr("tb.lock"), self)
        self._sep(self.row1)
        self.mesh_btn = self._btn(self.row1, "mesh", tr("tb.mesh"), window._toggle_mesh_or_build, tr("tip.mesh"))
        self.mesh_btn.setCheckable(True)
        if self.mesh_btn.defaultAction():
            self.mesh_btn.defaultAction().setCheckable(True)
        self._sep(self.row1)

        self.row1.addWidget(QLabel(tr("tb.tolerance") + ":"))
        self.tol_label = self.row1.itemAt(self.row1.count() - 1).widget()
        self.tol_value = DualDecimalSpinBox()
        self.tol_value.setDecimals(3)
        self.tol_value.setRange(0.001, 1e6)
        self.tol_value.setSingleStep(0.01)
        self.tol_value.setValue(window.controller.data.tolerances.match)
        self.tol_value.setMaximumWidth(84)
        self.tol_unit = QComboBox()
        self.tol_unit.addItems(["mm", "mil", "inch", "px"])
        cur = window.controller.data.tolerances.unit.value
        if self.tol_unit.findText(cur) < 0:
            cur = "mm"
        self.tol_unit.setCurrentText(cur)
        self.tol_value.editingFinished.connect(window._toolbar_tolerance)
        self.tol_unit.currentTextChanged.connect(lambda *_: window._toolbar_tolerance())
        self.row1.addWidget(self.tol_value)
        self.row1.addWidget(self.tol_unit)
        self.scale_status = QLabel(tr("tb.scale_not_set"))
        self.scale_status.setToolTip(tr("tip.scale_status"))
        self.row1.addWidget(self.scale_status)
        self.set_scale_btn = QPushButton(tr("tb.set_scale"))
        self.set_scale_btn.setCheckable(True)
        self.set_scale_btn.setToolTip(tr("tip.set_scale"))
        self.set_scale_btn.clicked.connect(window._toggle_set_scale)
        self.row1.addWidget(self.set_scale_btn)
        self._btn(self.row1, "calculate", tr("tb.calculate"), window._calculate, tr("tip.calculate"))
        self.row1.addStretch(1)

        # ROW 2 — view / draw / measure
        self._btn(self.row2, "log", tr("tb.log"), window._show_log, tr("tip.log"), "Ctrl+L")
        self._btn(self.row2, "fit", tr("tb.fit"), window.fit_both, tr("tip.fit"), "F")
        self._btn(self.row2, "1to1", tr("tb.1to1"), window.actual_size_both, tr("tip.1to1"))
        self._btn(self.row2, "reset_view", tr("tb.reset_view"), window.reset_views, tr("tip.reset_view"))
        self.sync_btn = QToolButton(self)
        self.sync_btn.setText(tr("tb.sync"))
        self.sync_btn.setCheckable(True)
        self.sync_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.sync_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.sync_btn.setToolTip(tr("tip.sync"))
        self.sync_menu = QMenu(self.sync_btn)
        self.act_sync_on = self.sync_menu.addAction(tr("sync.view"))
        self.act_sync_on.setCheckable(True)
        self.act_sync_zoom = self.sync_menu.addAction(tr("sync.zoom"))
        self.act_sync_zoom.setCheckable(True)
        self.act_sync_pan = self.sync_menu.addAction(tr("sync.pan"))
        self.act_sync_pan.setCheckable(True)
        self.act_sync_cursor = self.sync_menu.addAction(tr("sync.cursor"))
        self.act_sync_cursor.setCheckable(True)
        self.act_sync_sel = self.sync_menu.addAction(tr("sync.selection"))
        self.act_sync_sel.setCheckable(True)
        self.sync_btn.setMenu(self.sync_menu)
        self.sync_btn.clicked.connect(window.toggle_sync)
        self.act_sync_on.triggered.connect(window.toggle_sync)
        self.act_sync_zoom.toggled.connect(lambda v: setattr(window.controller, "sync_zoom", v))
        self.act_sync_pan.toggled.connect(lambda v: setattr(window.controller, "sync_pan", v))
        self.act_sync_cursor.toggled.connect(lambda v: setattr(window.controller, "sync_cursor", v))
        self.act_sync_sel.toggled.connect(lambda v: setattr(window.controller, "sync_selection", v))
        self.row2.addWidget(self.sync_btn)
        self.sync = self.act_sync_on
        self.overlay_btn = QToolButton(self)
        self.overlay_btn.setText(tr("tb.overlay"))
        self.overlay_btn.setCheckable(True)
        self.overlay_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.overlay_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.overlay_btn.setToolTip(tr("tip.overlay"))
        self.overlay_menu = QMenu(self.overlay_btn)
        self.act_ov_off = self.overlay_menu.addAction(tr("ov.off"))
        self.act_ov_off.setCheckable(True)
        self.act_ov_off.setChecked(True)
        self.act_ov_ab = self.overlay_menu.addAction(tr("ov.a_over_b"))
        self.act_ov_ab.setCheckable(True)
        self.act_ov_ba = self.overlay_menu.addAction(tr("ov.b_over_a"))
        self.act_ov_ba.setCheckable(True)
        self.overlay_menu.addSeparator()
        self.act_ov_50 = self.overlay_menu.addAction(tr("ov.fifty"))
        self.act_ov_reset = self.overlay_menu.addAction(tr("ov.reset_opacity"))
        self.overlay_btn.setMenu(self.overlay_menu)
        self.overlay_btn.clicked.connect(window.toggle_overlay)
        self.act_ov_off.triggered.connect(window.close_overlay)
        self.act_ov_ab.triggered.connect(lambda: window.show_overlay_mode(OverlayMode.A_OVER_B))
        self.act_ov_ba.triggered.connect(lambda: window.show_overlay_mode(OverlayMode.B_OVER_A))
        self.act_ov_50.triggered.connect(lambda: window._overlay_preset("mid"))
        self.act_ov_reset.triggered.connect(lambda: window._overlay_preset("reset_op"))
        self.row2.addWidget(self.overlay_btn)
        self._buttons["overlay"] = self.overlay_btn
        self._sep(self.row2)
        self._tool(self.row2, ActiveTool.SELECT, tr("tb.select"), None, tr("tip.select"))
        self._tool(self.row2, ActiveTool.DIMENSION, tr("tb.dimension"), "D", tr("tip.dimension"))
        self._tool(self.row2, ActiveTool.CONTOUR, tr("tb.contour"), None, tr("tip.contour"))
        self._tool(self.row2, ActiveTool.CROP, tr("tb.crop"), None, tr("tip.crop"))
        self._btn(self.row2, "adjustments", tr("tb.adjustments"), lambda: window.show_dock("adjustments"), "")
        self._sep(self.row2)
        if hasattr(window, "act_undo"):
            self.undo_btn = QToolButton()
            self.undo_btn.setDefaultAction(window.act_undo)
            self.row2.addWidget(self.undo_btn)
            self.redo_btn = QToolButton()
            self.redo_btn.setDefaultAction(window.act_redo)
            self.row2.addWidget(self.redo_btn)
        self._sep(self.row2)
        self._btn(self.row2, "save", tr("tb.save"), window._save, tr("tip.save"), "Ctrl+S")
        self.apply_crop_btn = QPushButton(tr("crop.keep"))
        self.cancel_crop_btn = QPushButton(tr("crop.cancel"))
        self.reset_crop_btn = QPushButton(tr("crop.reset"))
        self.edit_crop_btn = QPushButton(tr("crop.edit"))
        self.apply_crop_btn.clicked.connect(lambda: window._apply_crop())
        self.cancel_crop_btn.clicked.connect(lambda: window._cancel_crop())
        self.reset_crop_btn.clicked.connect(lambda: window._reset_crop())
        self.edit_crop_btn.clicked.connect(lambda: window._edit_crop())
        for b in (self.apply_crop_btn, self.cancel_crop_btn, self.reset_crop_btn, self.edit_crop_btn):
            self.row2.addWidget(b)
            b.hide()
        self.row2.addStretch(1)

    def _orient_btn(self, row, side: Side, key: str) -> QToolButton:
        btn = QToolButton()
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        prefix = tr("tb.orient_a") if side == Side.A else tr("tb.orient_b")
        btn.setText(f"{prefix}: {tr('or.normal')}")
        btn.setToolTip(tr("tip.mirror_a") if side == Side.A else tr("tip.mirror_b"))
        menu = QMenu(btn)
        acts = []
        def add(key, slot):
            act = menu.addAction(tr(key))
            act.triggered.connect(slot)
            acts.append((act, key))

        add("or.normal", lambda: self.window._set_orient_side(side, OrientationName.NORMAL))
        add("or.mirror_x", lambda: self.window._set_orient_side(side, OrientationName.MIRROR_X))
        add("or.mirror_y", lambda: self.window._set_orient_side(side, OrientationName.MIRROR_Y))
        add("or.rot90", lambda: self.window._set_orient_side(side, OrientationName.ROTATE_90))
        add("or.rot180", lambda: self.window._set_orient_side(side, OrientationName.ROTATE_180))
        add("or.rot270", lambda: self.window._set_orient_side(side, OrientationName.ROTATE_270))
        add("or.reset", lambda: self.window.reset_orient(side))
        menu.addSeparator()
        add("or.display_x", lambda: self.window.display_mirror(side, "x"))
        add("or.display_y", lambda: self.window.display_mirror(side, "y"))
        btn._orient_acts = acts
        btn.setMenu(menu)
        btn.clicked.connect(lambda: self.window.mirror_image(side, "x"))
        row.addWidget(btn)
        self._buttons[key] = btn
        if key == "mirror_a":
            self._orient_menu_a = menu
        else:
            self._orient_menu_b = menu
        return btn

    def _sep(self, row: QHBoxLayout) -> None:
        lab = QLabel("│")
        lab.setStyleSheet("color:#666; padding:0 2px;")
        row.addWidget(lab)

    def _btn(self, row, key: str, text: str, slot, tip: str, shortcut: str | None = None) -> QToolButton:
        btn = QToolButton()
        btn.setText(text)
        btn.setToolTip(tip)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        action = QAction(text, self)
        action.setToolTip(tip)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        btn.setDefaultAction(action)
        btn.setText(text)
        row.addWidget(btn)
        self._buttons[key] = btn
        return btn

    def _tool(self, row, tool: ActiveTool, text: str, shortcut, tip: str) -> QToolButton:
        action = QAction(text, self)
        action.setCheckable(True)
        action.setToolTip(tip)
        if shortcut:
            action.setShortcut(shortcut)
        action.triggered.connect(lambda checked=False, t=tool: self.window._tool_clicked(t))
        self._tool_group.addAction(action)
        self.actions_by_tool[tool] = action
        btn = QToolButton()
        btn.setDefaultAction(action)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        row.addWidget(btn)
        self._buttons[tool.value.lower()] = btn
        return btn

    def retranslate(self) -> None:
        mapping = {
            "open_a": ("tb.open_a", "tip.open_a"),
            "open_b": ("tb.open_b", "tip.open_b"),
            "paste_a": ("tb.paste_a", "tip.paste_a"),
            "paste_b": ("tb.paste_b", "tip.paste_b"),
            "zero": ("tb.zero", "tip.zero"),
            "scale": ("tb.scale", "tip.scale"),
            "control": ("tb.control", "tip.control"),
            "mirror_a": ("tb.orient_a", "tip.mirror_a"),
            "mirror_b": ("tb.orient_b", "tip.mirror_b"),
            "accept": ("tb.accept", "tip.accept"),
            "lock": ("tb.lock", "tip.lock"),
            "mesh": ("tb.mesh", "tip.mesh"),
            "calculate": ("tb.calculate", "tip.calculate"),
            "log": ("tb.log", "tip.log"),
            "fit": ("tb.fit", "tip.fit"),
            "1to1": ("tb.1to1", "tip.1to1"),
            "reset_view": ("tb.reset_view", "tip.reset_view"),
            "overlay": ("tb.overlay", "tip.overlay"),
            "mesh": ("tb.mesh", "tip.mesh"),
            "select": ("tb.select", "tip.select"),
            "dimension": ("tb.dimension", "tip.dimension"),
            "contour": ("tb.contour", "tip.contour"),
            "crop": ("tb.crop", "tip.crop"),
            "adjustments": ("tb.adjustments", ""),
            "save": ("tb.save", "tip.save"),
        }
        for key, (tk, tp) in mapping.items():
            btn = self._buttons.get(key)
            if btn is None:
                continue
            act = btn.defaultAction()
            if act:
                act.setText(tr(tk))
                if tp:
                    act.setToolTip(tr(tp))
            btn.setText(tr(tk))
        self.overlay_btn.setToolTip(tr("tip.overlay"))
        self.act_ov_off.setText(tr("ov.off"))
        self.act_ov_ab.setText(tr("ov.a_over_b"))
        self.act_ov_ba.setText(tr("ov.b_over_a"))
        self.act_ov_50.setText(tr("ov.fifty"))
        self.act_ov_reset.setText(tr("ov.reset_opacity"))
        self.sync_btn.setText(tr("tb.sync"))
        self.sync_btn.setToolTip(tr("tip.sync"))
        self.act_sync_on.setText(tr("sync.view"))
        self.act_sync_zoom.setText(tr("sync.zoom"))
        self.act_sync_pan.setText(tr("sync.pan"))
        self.act_sync_cursor.setText(tr("sync.cursor"))
        self.act_sync_sel.setText(tr("sync.selection"))
        self.set_scale_btn.setText(tr("tb.set_scale"))
        if hasattr(self, "tol_label") and self.tol_label:
            self.tol_label.setText(tr("tb.tolerance") + ":")
        self.apply_crop_btn.setText(tr("crop.keep"))
        self.cancel_crop_btn.setText(tr("crop.cancel"))
        self.reset_crop_btn.setText(tr("crop.reset"))
        if hasattr(self, "edit_crop_btn"):
            self.edit_crop_btn.setText(tr("crop.edit"))
        for key in ("mirror_a", "mirror_b"):
            btn = self._buttons.get(key)
            if btn is None:
                continue
            for act, ak in getattr(btn, "_orient_acts", []):
                act.setText(tr(ak))
        if hasattr(self.window, "_refresh_orient_buttons"):
            self.window._refresh_orient_buttons()
        txt = self.scale_status.text()
        if "NOT SET" in txt or "НЕ ЗАДАН" in txt or "Scale" in txt or "Масштаб" in txt:
            self.scale_status.setText(tr("tb.scale_not_set"))

    def sync_button_global_rect(self):
        return self.sync_btn.rect(), self.sync_btn.mapToGlobal(QPoint(0, 0))
