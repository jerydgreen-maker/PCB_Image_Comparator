"""SET SCALE — two clicks plus a known millimetre distance. Nothing else."""

from __future__ import annotations

from app.i18n import tr
from ui.decimal_spin import DualDecimalSpinBox
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QVBoxLayout,
)


class KnownDistanceDialog(QDialog):
    def __init__(self, pixel_distance: float, parent=None) -> None:
        super().__init__(parent)
        self._pixel_distance = float(pixel_distance)
        self.setWindowTitle(tr("dlg.known_distance"))
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.distance = DualDecimalSpinBox()
        self.distance.setDecimals(4)
        self.distance.setRange(0.0001, 1e6)
        self.distance.setValue(10.0)
        self.distance.setSuffix(" mm")
        form.addRow(tr("dlg.distance"), self.distance)
        layout.addLayout(form)
        btns = QDialogButtonBox()
        apply_btn = btns.addButton(tr("btn.apply"), QDialogButtonBox.ButtonRole.AcceptRole)
        btns.addButton(QDialogButtonBox.StandardButton.Cancel)
        apply_btn.clicked.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def known_mm(self) -> float:
        return float(self.distance.value())
