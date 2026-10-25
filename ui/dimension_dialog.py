"""Edit an existing CAD dimension. Endpoints stay fixed unless the user deletes it."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)

from models.dimension import DimensionOrientation


class DimensionEditDialog(QDialog):
    def __init__(self, controller, dim_id: str, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.dim_id = dim_id
        dim = next((d for d in controller.data.dimensions if d.id == dim_id), None)
        self.setWindowTitle(f"Dimension {dim_id}")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.orient = QComboBox()
        for o in (DimensionOrientation.ALIGNED, DimensionOrientation.HORIZONTAL, DimensionOrientation.VERTICAL):
            self.orient.addItem(o, o)
        if dim:
            self.orient.setCurrentText(dim.orientation)
        self.prec = QSpinBox()
        self.prec.setRange(0, 6)
        self.prec.setValue(dim.precision if dim else 2)
        self.label = QLineEdit(dim.label if dim else "")
        self.note = QLineEdit(dim.note if dim else "")
        self.visible = QCheckBox("Visible")
        self.visible.setChecked(True if dim is None else dim.visible)
        self.project = QCheckBox("Show corresponding dimension on other image")
        self.project.setChecked(bool(dim and dim.show_on_other))
        form.addRow("Orientation", self.orient)
        form.addRow("Precision", self.prec)
        form.addRow("Name", self.label)
        form.addRow("Note", self.note)
        form.addRow(self.visible)
        form.addRow(self.project)
        layout.addLayout(form)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._apply)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _apply(self) -> None:
        c = self.controller
        c.set_dimension_orientation(self.dim_id, self.orient.currentData())
        c.set_dimension_precision(self.dim_id, self.prec.value())
        c.rename_dimension(self.dim_id, self.label.text(), self.note.text())
        c.set_dimension_visible(self.dim_id, self.visible.isChecked())
        if self.project.isChecked():
            dim = next((d for d in c.data.dimensions if d.id == self.dim_id), None)
            if dim and not dim.linked_id:
                c.project_dimension_to_other(self.dim_id)
        self.accept()
