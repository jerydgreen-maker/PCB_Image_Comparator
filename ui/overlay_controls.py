from __future__ import annotations

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QFormLayout, QLabel, QSlider, QWidget

from app.i18n import tr
from models.enums import OverlayMode


_LABELS = {
    OverlayMode.A_ONLY: "ov.off",
    OverlayMode.B_ONLY: "tb.open_b",
    OverlayMode.OVERLAY: "ov.b_over_a",
    OverlayMode.B_OVER_A: "ov.b_over_a",
    OverlayMode.A_OVER_B: "ov.a_over_b",
    OverlayMode.DIFFERENCE: "st.diff",
    OverlayMode.EDGE_DIFFERENCE: "st.edge",
    OverlayMode.FLICKER: "adj.cmp_flicker",
}


class OverlayControls(QWidget):
    changed = Signal()

    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        form = QFormLayout(self)
        self.mode = QComboBox()
        for m in OverlayMode:
            self.mode.addItem(tr(_LABELS.get(m, "ov.off")), m)
        self.mode.currentIndexChanged.connect(self._apply)
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.setValue(int(controller.data.overlay_opacity * 100))
        self.opacity.valueChanged.connect(self._apply)
        self.flicker = QDoubleSpinBox()
        self.flicker.setRange(0.2, 20.0)
        self.flicker.setValue(controller.data.flicker_hz)
        self.flicker.valueChanged.connect(self._apply)
        form.addRow(tr("ov.title"), self.mode)
        form.addRow(tr("ov.opacity"), self.opacity)
        form.addRow(tr("adj.cmp_flicker"), self.flicker)
        form.addRow(QLabel(tr("ov.need_align")))

    def _apply(self) -> None:
        self.controller.data.overlay_mode = self.mode.currentData()
        self.controller.data.overlay_opacity = self.opacity.value() / 100.0
        self.controller.data.flicker_hz = self.flicker.value()
        self.changed.emit()
