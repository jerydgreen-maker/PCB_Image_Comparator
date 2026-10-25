from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
)

from models.enums import Side, Unit


class CalibrationDialog(QDialog):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Image Calibration")
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Each image has an independent scale. Calibration is never copied "
                "unless you explicitly request it. Click two points with the Calibrate tool first."
            )
        )
        form = QFormLayout()
        self.side = QComboBox()
        self.side.addItems(["A", "B"])
        self.length = QDoubleSpinBox()
        self.length.setDecimals(4)
        self.length.setRange(0.0001, 1e6)
        self.length.setValue(10.0)
        self.unit = QComboBox()
        for u in Unit:
            if u != Unit.PX:
                self.unit.addItem(u.value, u)
        self.unc = QDoubleSpinBox()
        self.unc.setDecimals(4)
        self.unc.setRange(0.0, 1e6)
        self.unc.setValue(0.15)
        self.pick = QDoubleSpinBox()
        self.pick.setDecimals(2)
        self.pick.setRange(0.0, 50.0)
        self.pick.setValue(1.0)
        self.copy = QCheckBox("Also copy this calibration to the other image (explicit)")
        form.addRow("Image", self.side)
        form.addRow("Known length", self.length)
        form.addRow("Unit", self.unit)
        form.addRow("Length uncertainty", self.unc)
        form.addRow("Point-selection uncertainty (px)", self.pick)
        layout.addLayout(form)
        layout.addWidget(self.copy)
        self.info = QLabel("")
        layout.addWidget(self.info)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._apply)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        self._refresh_info()
        self.side.currentTextChanged.connect(lambda _: self._refresh_info())

    def _refresh_info(self) -> None:
        side = Side.A if self.side.currentText() == "A" else Side.B
        img = self.controller.image(side)
        if img is None:
            self.info.setText(f"Image {side.value} is not loaded.")
            return
        pts = img.derivation.get("_cal_pts") or []
        extra = f"Calibration clicks: {len(pts)}"
        if img.calibration.is_calibrated:
            extra += (
                f"\nCurrent scale: {img.calibration.mm_per_px:.6f} mm/px  "
                f"({img.calibration.px_per_mm:.4f} px/mm)"
            )
        self.info.setText(extra)

    def _apply(self) -> None:
        side = Side.A if self.side.currentText() == "A" else Side.B
        img = self.controller.image(side)
        if img:
            img.calibration.point_selection_px = self.pick.value()
        self.controller.finish_calibration(
            side, self.length.value(), self.unit.currentData(), self.unc.value() or None
        )
        if self.copy.isChecked():
            other = Side.B if side == Side.A else Side.A
            self.controller.copy_calibration(side, other)
        self.accept()
