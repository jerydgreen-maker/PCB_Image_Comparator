from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QDoubleSpinBox,
)

from app.i18n import tr
from models.enums import Confidence, Side, TransformMode


class ContourPanel(QWidget):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        layout.addWidget(self.list)
        row = QHBoxLayout()
        self._top_btns = []
        for key, slot in (
            ("ctr.close_a", lambda: controller.close_active_contour(Side.A)),
            ("ctr.close_b", lambda: controller.close_active_contour(Side.B)),
            ("ctr.undo_a", lambda: controller.undo_contour_point(Side.A)),
            ("ctr.undo_b", lambda: controller.undo_contour_point(Side.B)),
        ):
            b = QPushButton(tr(key))
            b.setProperty("i18n", key)
            b.clicked.connect(slot)
            row.addWidget(b)
            self._top_btns.append(b)
        layout.addLayout(row)
        self.partial = QCheckBox(tr("ctr.partial"))
        self.cropped = QCheckBox(tr("ctr.cropped"))
        self.unverified = QCheckBox(tr("ctr.unverified"))
        self.inferred = QCheckBox(tr("ctr.inferred"))
        for box, attr in (
            (self.partial, "include_partial_in_stats"),
            (self.cropped, "include_cropped_in_stats"),
            (self.unverified, "include_unverified_in_stats"),
            (self.inferred, "include_inferred_in_stats"),
        ):
            box.setChecked(getattr(controller.data, attr))
            box.toggled.connect(lambda v, a=attr: setattr(self.controller.data, a, v))
            layout.addWidget(box)
        self.mode = QComboBox()
        self.mode.addItem("Rigid", TransformMode.RIGID)
        self.mode.addItem("Similarity", TransformMode.SIMILARITY)
        self.reg_lab = QLabel(tr("ctr.reg"))
        layout.addWidget(self.reg_lab)
        layout.addWidget(self.mode)
        edit = QHBoxLayout()
        self.vindex = QSpinBox()
        self.vindex.setRange(0, 99999)
        self.vx = QDoubleSpinBox()
        self.vx.setRange(-1e7, 1e7)
        self.vx.setDecimals(3)
        self.vy = QDoubleSpinBox()
        self.vy.setRange(-1e7, 1e7)
        self.vy.setDecimals(3)
        self.vtx_lab = QLabel(tr("ctr.vertex"))
        edit.addWidget(self.vtx_lab)
        edit.addWidget(self.vindex)
        edit.addWidget(self.vx)
        edit.addWidget(self.vy)
        layout.addLayout(edit)
        erow = QHBoxLayout()
        self._edit_btns = []
        for key, slot in (
            ("ctr.insert", self._insert),
            ("ctr.move", self._move),
            ("ctr.delete", self._delete),
            ("ctr.split", self._split),
            ("ctr.cubic", self._cubic),
            ("ctr.reverse", self._reverse),
            ("ctr.open", self._open),
            ("ctr.close", self._close),
            ("ctr.project", self._project),
            ("ctr.join", self._join),
        ):
            b = QPushButton(tr(key))
            b.setProperty("i18n", key)
            b.clicked.connect(slot)
            erow.addWidget(b)
            self._edit_btns.append(b)
        layout.addLayout(erow)
        self.vector_density = QSpinBox()
        self.vector_density.setRange(1, 50)
        self.vector_density.setValue(4)
        self.dens_lab = QLabel(tr("ctr.density"))
        layout.addWidget(self.dens_lab)
        layout.addWidget(self.vector_density)
        self.report = QLabel(tr("ctr.stats"))
        self.report.setWordWrap(True)
        layout.addWidget(self.report)
        self.refresh()

    def retranslate(self) -> None:
        for b in getattr(self, "_top_btns", []) + getattr(self, "_edit_btns", []):
            key = b.property("i18n")
            if key:
                b.setText(tr(key))
        self.partial.setText(tr("ctr.partial"))
        self.cropped.setText(tr("ctr.cropped"))
        self.unverified.setText(tr("ctr.unverified"))
        self.inferred.setText(tr("ctr.inferred"))
        self.reg_lab.setText(tr("ctr.reg"))
        self.vtx_lab.setText(tr("ctr.vertex"))
        self.dens_lab.setText(tr("ctr.density"))
        self.report.setText(tr("ctr.stats"))

    def _selected(self):
        item = self.list.currentItem()
        if item is None:
            return None
        cid = item.text().split()[0]
        return next((c for c in self.controller.data.contours if c.id == cid), None)

    def _insert(self) -> None:
        c = self._selected()
        if c:
            self.controller.insert_contour_vertex(c.id, self.vindex.value(), self.vx.value(), self.vy.value())

    def _move(self) -> None:
        c = self._selected()
        if c:
            self.controller.move_contour_vertex(c.id, self.vindex.value(), self.vx.value(), self.vy.value())

    def _delete(self) -> None:
        c = self._selected()
        if c:
            self.controller.delete_contour_vertex(c.id, self.vindex.value())

    def _split(self) -> None:
        c = self._selected()
        if c:
            self.controller.split_contour_segment(c.id, self.vindex.value())

    def _cubic(self) -> None:
        c = self._selected()
        if c:
            self.controller.convert_contour_segment(c.id, self.vindex.value(), "cubic")

    def _reverse(self) -> None:
        c = self._selected()
        if c:
            self.controller.reverse_contour(c.id)

    def _open(self) -> None:
        c = self._selected()
        if c:
            self.controller.open_contour(c.id)

    def _close(self) -> None:
        c = self._selected()
        if c:
            self.controller.close_active_contour(c.side)

    def _project(self) -> None:
        c = self._selected()
        if c:
            self.controller.project_contour_to_other(c.id)

    def _join(self) -> None:
        c = self._selected()
        if c is None:
            return
        others = [x for x in self.controller.data.contours if x.side == c.side and x.id != c.id]
        if others:
            self.controller.join_contours(c.id, others[0].id)

    def refresh(self) -> None:
        self.list.clear()
        for c in self.controller.data.contours:
            self.list.addItem(
                f"{c.id} [{c.side.value}] {c.label} n={len(c.vertices)} "
                f"{'closed' if c.closed else 'open'} {c.source}"
            )
