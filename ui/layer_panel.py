from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QLabel, QVBoxLayout, QWidget

from app.i18n import tr
from models.enums import LayerName, MeshViewMode, Side


class LayerPanel(QWidget):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._boxes: dict[str, QCheckBox] = {}
        self._vis: dict[str, QCheckBox] = {}
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("vis.hint")))
        for key, label_key in (
            ("zero_scale", "vis.zero_scale"),
            ("control", "vis.control"),
            ("ids", "vis.ids"),
            ("expected", "vis.expected"),
            ("actual", "vis.actual"),
            ("residuals", "vis.residuals"),
            ("mesh", "vis.mesh"),
            ("dimensions", "vis.dimensions"),
            ("contours", "vis.contours"),
            ("measurements", "vis.measurements"),
        ):
            box = QCheckBox(tr(label_key))
            box.setChecked(True)
            box.toggled.connect(self._apply_project)
            layout.addWidget(box)
            self._vis[key] = box
        layout.addWidget(QLabel(tr("vis.pcb")))
        for layer in LayerName:
            box = QCheckBox(layer.value.replace("_", " "))
            box.setChecked(True)
            box.toggled.connect(self._apply)
            layout.addWidget(box)
            self._boxes[layer.value] = box
        layout.addStretch()
        self.sync_from_project()

    def sync_from_project(self) -> None:
        d = self.controller.data
        mesh = d.mesh
        mapping = {
            "zero_scale": d.show_zero_scale,
            "control": mesh.show_points,
            "ids": mesh.show_ids,
            "expected": mesh.show_expected,
            "actual": mesh.show_actual,
            "residuals": mesh.show_vectors,
            "mesh": mesh.view_mode != MeshViewMode.HIDDEN.value,
            "dimensions": d.show_dimensions,
            "contours": d.show_contours,
            "measurements": getattr(d, "show_measurements", True),
        }
        for key, val in mapping.items():
            box = self._vis.get(key)
            if box is None:
                continue
            box.blockSignals(True)
            box.setChecked(bool(val))
            box.blockSignals(False)

    def _apply_project(self) -> None:
        d = self.controller.data
        mesh = d.mesh
        d.show_zero_scale = self._vis["zero_scale"].isChecked()
        mesh.show_points = self._vis["control"].isChecked()
        mesh.show_ids = self._vis["ids"].isChecked()
        mesh.show_expected = self._vis["expected"].isChecked()
        mesh.show_actual = self._vis["actual"].isChecked()
        mesh.show_vectors = self._vis["residuals"].isChecked()
        d.show_dimensions = self._vis["dimensions"].isChecked()
        d.show_contours = self._vis["contours"].isChecked()
        d.show_measurements = self._vis["measurements"].isChecked()
        if self._vis["mesh"].isChecked():
            if mesh.view_mode == MeshViewMode.HIDDEN.value:
                mesh.view_mode = MeshViewMode.GHOST.value
        else:
            mesh.view_mode = MeshViewMode.HIDDEN.value
        self.controller.pointsChanged.emit()

    def _apply(self) -> None:
        for side in (Side.A, Side.B):
            img = self.controller.image(side)
            if img and img.vector:
                for name, box in self._boxes.items():
                    img.vector.layer_visible[name] = box.isChecked()
        self.controller.imagesChanged.emit()
