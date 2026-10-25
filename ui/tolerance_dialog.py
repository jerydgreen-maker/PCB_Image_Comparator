"""Tolerance manager — px / physical units, group overrides, example presets."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from models.enums import Unit
from models.tolerance import EXAMPLE_PRESETS


class ToleranceDialog(QDialog):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Tolerance Manager")
        self.resize(440, 560)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("User-defined limits. Example presets are not engineering specifications."))

        form = QFormLayout()
        self.value = QDoubleSpinBox()
        self.value.setDecimals(4)
        self.value.setRange(0.0001, 1e6)
        self.value.setValue(controller.data.tolerances.match)
        self.unit = QComboBox()
        self.unit.addItems([u.value for u in Unit])
        self.unit.setCurrentText(controller.data.tolerances.unit.value)
        form.addRow("GLOBAL tolerance", self.value)
        form.addRow("Unit", self.unit)
        layout.addLayout(form)

        groups = QGroupBox("Per-group overrides (empty = inherit GLOBAL)")
        gform = QFormLayout(groups)
        self.group_spins: dict[str, QDoubleSpinBox] = {}
        for g in controller.data.groups:
            spin = QDoubleSpinBox()
            spin.setDecimals(4)
            spin.setRange(0.0, 1e6)
            spin.setSpecialValueText("inherit")
            spin.setValue(controller.data.tolerances.group_match.get(g.name, 0.0))
            self.group_spins[g.name] = spin
            gform.addRow(g.name, spin)
        layout.addWidget(groups)

        adv = QGroupBox("Advanced (optional, separate unit types — never mixed)")
        aform = QFormLayout(adv)
        self.pitch = _opt_spin(controller.data.tolerances.pitch)
        self.length = _opt_spin(controller.data.tolerances.length)
        self.angle = _opt_spin(controller.data.tolerances.angle_deg, suffix=" °")
        self.scale = _opt_spin(controller.data.tolerances.scale_pct, suffix=" %")
        self.x_tol = _opt_spin(controller.data.tolerances.x_tol)
        self.y_tol = _opt_spin(controller.data.tolerances.y_tol)
        aform.addRow("Pitch tolerance", self.pitch)
        aform.addRow("Length / edge tolerance", self.length)
        aform.addRow("Angle tolerance", self.angle)
        aform.addRow("Scale tolerance", self.scale)
        aform.addRow("X tolerance", self.x_tol)
        aform.addRow("Y tolerance", self.y_tol)
        layout.addWidget(adv)

        self.circles = QCheckBox("Draw tolerance circle around expected CONTROL")
        self.circles.setChecked(controller.data.tolerances.show_circles)
        layout.addWidget(self.circles)

        preset_row = QHBoxLayout()
        self.preset = QComboBox()
        self.preset.addItem("(example presets — not authoritative)")
        for name in EXAMPLE_PRESETS:
            self.preset.addItem(name)
        apply_p = QPushButton("Load example")
        apply_p.clicked.connect(self._load_example)
        preset_row.addWidget(self.preset, 1)
        preset_row.addWidget(apply_p)
        layout.addLayout(preset_row)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._apply)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _load_example(self) -> None:
        data = EXAMPLE_PRESETS.get(self.preset.currentText())
        if not data:
            return
        self.value.setValue(float(data["match"]))
        self.unit.setCurrentText(data["unit"])

    def _apply(self) -> None:
        unit = Unit(self.unit.currentText())
        if not self.controller.set_compare_tolerance(self.value.value(), unit):
            if unit != Unit.PX:
                ans = QMessageBox.question(
                    self,
                    "Calibration required",
                    "Physical tolerance requires image calibration.\n\nCalibrate now, or switch unit to pixels?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if ans == QMessageBox.StandardButton.Yes:
                    self.done(2)
                    return
                self.unit.setCurrentText(Unit.PX.value)
                return
            return
        for name, spin in self.group_spins.items():
            self.controller.set_group_tolerance(name, None if spin.value() <= 0 else spin.value())
        t = self.controller.data.tolerances
        t.pitch = _opt_val(self.pitch)
        t.length = _opt_val(self.length)
        t.angle_deg = _opt_val(self.angle)
        t.scale_pct = _opt_val(self.scale)
        t.x_tol = _opt_val(self.x_tol)
        t.y_tol = _opt_val(self.y_tol)
        t.show_circles = self.circles.isChecked()
        self.accept()


def _opt_spin(value: float | None, suffix: str = "") -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setDecimals(4)
    spin.setRange(0.0, 1e6)
    spin.setSpecialValueText("off")
    if suffix:
        spin.setSuffix(suffix)
    spin.setValue(0.0 if value is None else float(value))
    return spin


def _opt_val(spin: QDoubleSpinBox) -> float | None:
    return None if spin.value() <= 0 else float(spin.value())
