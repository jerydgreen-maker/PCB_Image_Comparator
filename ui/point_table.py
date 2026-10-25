from __future__ import annotations

from PySide6.QtCore import QItemSelection, QItemSelectionModel, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.i18n import tr
from models.enums import Confidence, PointType, Side
from models.units import px_to_unit

HEADERS = [
    "Visible",
    "ID",
    "Type",
    "Group",
    "Side",
    "Status",
    "Confidence",
    "Error",
    "Notes",
]

ROLE = Qt.ItemDataRole.UserRole
FILTERS = ("all", "control", "contours", "dimensions", "mesh", "measurements", "registration")


class PointTable(QWidget):
    zoomTo = Signal(object)
    changed = Signal()
    centerA = Signal(object)
    centerB = Signal(object)
    centerBoth = Signal(object)
    resetActual = Signal(str)
    hideIds = Signal(list)
    managerSelect = Signal(list)
    showObject = Signal(object)
    actionDelete = Signal()

    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.filter = QComboBox()
        for key in FILTERS:
            self.filter.addItem(tr(f"sel.filter_{key}"), key)
        self.filter.currentIndexChanged.connect(lambda *_: self.refresh())
        top.addWidget(self.filter)
        top.addStretch()
        layout.addLayout(top)
        btns = QHBoxLayout()
        for text_key, slot in (
            ("sel.hide", self._hide),
            ("sel.show", self._show),
            ("sel.delete", self._delete),
            ("sel.reset_actual", self._reset_actual),
            ("sel.center_a", self._center_a),
            ("sel.center_b", self._center_b),
            ("sel.center_both", self._center_both),
        ):
            b = QPushButton(tr(text_key))
            b.setProperty("en_text", text_key)
            b.clicked.connect(slot)
            btns.addWidget(b)
        btns.addStretch()
        layout.addLayout(btns)
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.table.cellDoubleClicked.connect(self._double)
        self.table.itemChanged.connect(self._edit)
        self.table.itemSelectionChanged.connect(self._sel_changed)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        layout.addWidget(self.table)
        self._updating = False
        self._rows: list[tuple] = []

    def retranslate(self) -> None:
        cur = self.filter.currentData()
        self.filter.blockSignals(True)
        self.filter.clear()
        for key in FILTERS:
            self.filter.addItem(tr(f"sel.filter_{key}"), key)
        idx = max(0, next((i for i in range(self.filter.count()) if self.filter.itemData(i) == cur), 0))
        self.filter.setCurrentIndex(idx)
        self.filter.blockSignals(False)
        self.table.setHorizontalHeaderLabels(HEADERS)

    def refresh(self, selected=None) -> None:
        keep = selected
        self._updating = True
        self.table.blockSignals(True)
        proj = self.controller.data
        unit = proj.display_unit
        cal_b = proj.image_b.calibration.mm_per_px if proj.image_b and proj.image_b.calibration.is_calibrated else None
        xf = self.controller.accepted_transform()
        filt = self.filter.currentData() or "all"
        rows = self._collect_rows(proj, xf, unit, cal_b, filt)
        self._rows = [r[0] for r in rows]
        self.table.setRowCount(len(rows))
        for row, (key, vals, editable) in enumerate(rows):
            for col, val in enumerate(vals):
                item = QTableWidgetItem(str(val))
                item.setData(ROLE, key)
                if col not in editable:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, col, item)
        self.table.blockSignals(False)
        self._updating = False
        if keep is not None and not isinstance(keep, int):
            self.select_items(keep)

    def _collect_rows(self, proj, xf, unit, cal_b, filt: str) -> list[tuple]:
        from core.analysis import control_match_state

        out: list[tuple] = []
        prec = proj.display_precision
        if filt in ("all", "control", "measurements", "registration"):
            for lm in proj.landmarks:
                kind = lm.point_type
                if filt == "control" and kind != PointType.CONTROL:
                    continue
                if filt == "measurements" and kind != PointType.MEASUREMENT:
                    continue
                if filt == "registration" and kind not in (PointType.ZERO, PointType.SCALE):
                    continue
                err = None
                if xf and lm.has_a and lm.has_b:
                    ax, ay = self.controller._work_xy(Side.A, lm.a_x, lm.a_y)
                    pred_x, pred_y = xf.apply_one(ax, ay)
                    bx, by = self.controller._work_xy(Side.B, lm.b_x, lm.b_y)
                    err = ((bx - pred_x) ** 2 + (by - pred_y) ** 2) ** 0.5
                try:
                    state = (
                        control_match_state(self.controller, lm)
                        if kind in (PointType.CONTROL, PointType.MEASUREMENT)
                        else kind.value
                    )
                except Exception:
                    state = lm.confidence.value
                side = "A+B" if lm.has_a and lm.has_b else ("A" if lm.has_a else ("B" if lm.has_b else "—"))
                key = ("landmark", lm.id, None)
                vals = [
                    "Y" if getattr(lm, "visible", True) else "N",
                    lm.id,
                    kind.value,
                    lm.group,
                    side,
                    state or "",
                    lm.confidence.value,
                    _fmt(px_to_unit(err, cal_b, unit) if err is not None else None, prec),
                    lm.notes,
                ]
                out.append((key, vals, {0, 3, 6, 8}))
        if filt in ("all", "contours"):
            for c in proj.contours:
                key = ("contour", c.id, None)
                vals = [
                    "Y" if c.visible else "N",
                    c.id,
                    "CONTOUR",
                    c.role or "",
                    c.side.value,
                    c.role or "",
                    "",
                    "",
                    getattr(c, "notes", "") or "",
                ]
                out.append((key, vals, {0}))
        if filt in ("all", "dimensions"):
            for d in proj.dimensions:
                key = ("dimension", d.id, None)
                vals = [
                    "Y" if d.visible else "N",
                    d.id,
                    "DIMENSION",
                    d.orientation,
                    d.side.value,
                    "",
                    "",
                    "",
                    d.note or "",
                ]
                out.append((key, vals, {0}))
        if filt in ("all", "mesh"):
            for e in proj.mesh.edges:
                if getattr(e, "excluded", False):
                    continue
                key = ("mesh_edge", e.a, e.b)
                vals = [
                    "Y",
                    f"{e.a}-{e.b}",
                    "MESH",
                    "",
                    "A+B",
                    "",
                    "",
                    "",
                    "",
                ]
                out.append((key, vals, set()))
        return out

    def select_items(self, items) -> None:
        want = {_key(it) for it in items}
        self._updating = True
        self.table.blockSignals(True)
        sm = self.table.selectionModel()
        if sm is not None:
            sm.clearSelection()
        else:
            self.table.clearSelection()
        first = None
        model = self.table.model()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            if item is None:
                continue
            if _key(item.data(ROLE)) in want:
                if sm is not None and model is not None:
                    left = model.index(row, 0)
                    right = model.index(row, self.table.columnCount() - 1)
                    sm.select(QItemSelection(left, right), QItemSelectionModel.SelectionFlag.Select)
                else:
                    self.table.selectRow(row)
                if first is None:
                    first = row
        if first is not None:
            self.table.setCurrentCell(first, 1)
        self.table.blockSignals(False)
        self._updating = False

    def current_items(self) -> list[tuple]:
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        if not rows and self.table.currentRow() >= 0:
            rows = [self.table.currentRow()]
        out = []
        for row in rows:
            item = self.table.item(row, 1)
            if item is None:
                continue
            key = item.data(ROLE)
            if key:
                out.append(tuple(key) if not isinstance(key, tuple) else key)
        return out

    def _current_id(self) -> str | None:
        for it in self.current_items():
            if it[0] == "landmark":
                return it[1]
        return None

    def _selected_ids(self) -> list[str]:
        return [it[1] for it in self.current_items() if it[0] == "landmark"]

    def _double(self, row: int, _col: int) -> None:
        item = self.table.item(row, 1)
        if item and item.data(ROLE):
            self.zoomTo.emit(tuple(item.data(ROLE)))

    def _sel_changed(self) -> None:
        if self._updating:
            return
        items = self.current_items()
        self.managerSelect.emit(items)

    def _edit(self, item: QTableWidgetItem) -> None:
        if self._updating:
            return
        key = item.data(ROLE)
        if not key:
            return
        key = tuple(key)
        col = item.column()
        text = item.text()
        if key[0] == "landmark":
            pid = key[1]
            lm = next((x for x in self.controller.data.landmarks if x.id == pid), None)
            if lm is None:
                return
            try:
                if col == 0:
                    self.controller.set_landmark_visible(pid, text.upper() in ("Y", "YES", "1", "TRUE"))
                elif col == 3:
                    self.controller.set_landmark_attr(pid, "group", text)
                elif col == 6:
                    self.controller.set_landmark_attr(pid, "confidence", Confidence(text))
                elif col == 8:
                    self.controller.set_landmark_attr(pid, "notes", text)
            except ValueError:
                self.refresh()
                return
            self.changed.emit()

    def _hide(self) -> None:
        ids = self._selected_ids()
        if ids:
            self.hideIds.emit(ids)

    def _show(self) -> None:
        items = self.current_items()
        for it in items:
            self.showObject.emit(it)
        if not items:
            for pid in self._selected_ids():
                self.controller.set_landmark_visible(pid, True)
        self.refresh()

    def _delete(self) -> None:
        self.actionDelete.emit()

    def _reset_actual(self) -> None:
        pid = self._current_id()
        if pid:
            self.resetActual.emit(pid)

    def _center_a(self) -> None:
        items = self.current_items()
        if items:
            self.centerA.emit(items[0])

    def _center_b(self) -> None:
        items = self.current_items()
        if items:
            self.centerB.emit(items[0])

    def _center_both(self) -> None:
        items = self.current_items()
        if items:
            self.centerBoth.emit(items[0])

    def _menu(self, pos) -> None:
        items = self.current_items()
        if not items:
            return
        menu = QMenu(self)
        menu.addAction(tr("sel.center_a")).triggered.connect(self._center_a)
        menu.addAction(tr("sel.center_b")).triggered.connect(self._center_b)
        menu.addAction(tr("sel.center_both")).triggered.connect(self._center_both)
        menu.addSeparator()
        menu.addAction(tr("sel.show")).triggered.connect(self._show)
        menu.addAction(tr("sel.hide")).triggered.connect(self._hide)
        menu.exec(self.table.viewport().mapToGlobal(pos))


def _key(item) -> tuple:
    if item is None:
        return ()
    try:
        t = item if isinstance(item, tuple) else tuple(item)
    except TypeError:
        return (item,)
    if not t:
        return ()
    if t[0] == "landmark":
        return ("landmark", t[1])
    if t[0] in ("dimension", "contour"):
        return (t[0], t[1])
    if t[0] == "mesh_edge" and len(t) >= 3:
        return ("mesh_edge",) + tuple(sorted((t[1], t[2])))
    return t


def _fmt(v, prec: int) -> str:
    if v is None:
        return "—"
    return f"{float(v):.{prec}f}"
