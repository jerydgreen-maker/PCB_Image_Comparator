from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout

from models.enums import AxisX, AxisY, OriginMode, Side


class BoardCoordinateDialog(QDialog):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Board coordinate system")
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Display convention only. Changing origin or axes does not alter the accepted transform."
            )
        )
        form = QFormLayout()
        self.origin = QComboBox()
        for m in OriginMode:
            self.origin.addItem(m.value, m)
        self.origin.setCurrentText(controller.data.board_cs.origin_mode.value)
        self.ax = QComboBox()
        for m in AxisX:
            self.ax.addItem("X " + m.value, m)
        self.ax.setCurrentIndex(0 if controller.data.board_cs.axis_x == AxisX.RIGHT else 1)
        self.ay = QComboBox()
        for m in AxisY:
            self.ay.addItem("Y " + m.value, m)
        self.ay.setCurrentIndex(0 if controller.data.board_cs.axis_y == AxisY.DOWN else 1)
        form.addRow("Origin", self.origin)
        form.addRow("X axis", self.ax)
        form.addRow("Y axis", self.ay)
        layout.addLayout(form)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._apply)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _apply(self) -> None:
        cs = self.controller.data.board_cs
        cs.origin_mode = self.origin.currentData()
        cs.axis_x = self.ax.currentData()
        cs.axis_y = self.ay.currentData()
        img = self.controller.image(Side.A)
        if img and cs.origin_mode == OriginMode.TOP_LEFT:
            cs.origin_x, cs.origin_y = 0.0, 0.0
        elif img and cs.origin_mode == OriginMode.BOTTOM_LEFT:
            cs.origin_x, cs.origin_y = 0.0, float(img.height - 1)
        elif img and cs.origin_mode == OriginMode.CENTROID:
            cs.origin_x, cs.origin_y = img.width / 2.0, img.height / 2.0
        self.controller.mark_dirty()
        self.accept()
