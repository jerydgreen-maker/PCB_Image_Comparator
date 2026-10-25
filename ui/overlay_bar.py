"""Overlay strip: direction, top-image opacity, presets, close."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QWidget

from app.i18n import tr
from models.enums import OverlayMode


class OverlayBar(QWidget):
    opacityChanged = Signal(int)
    preset = Signal(str)
    closed = Signal()
    directionChanged = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("overlayBar")
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        self.title = QLabel(tr("ov.title"))
        row.addWidget(self.title)
        self.dir_a = QPushButton(tr("ov.a_over_b"))
        self.dir_b = QPushButton(tr("ov.b_over_a"))
        self.dir_a.setCheckable(True)
        self.dir_b.setCheckable(True)
        self.dir_a.clicked.connect(lambda: self.directionChanged.emit(OverlayMode.A_OVER_B))
        self.dir_b.clicked.connect(lambda: self.directionChanged.emit(OverlayMode.B_OVER_A))
        row.addWidget(self.dir_a)
        row.addWidget(self.dir_b)
        self.op_lab = QLabel(tr("ov.opacity"))
        row.addWidget(self.op_lab)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(50)
        self.slider.setMinimumWidth(140)
        self.pct = QLabel("50%")
        self.slider.valueChanged.connect(self._slide)
        row.addWidget(self.slider, 1)
        row.addWidget(self.pct)
        self._preset_btns = []
        for pct in (0, 25, 50, 75, 100):
            btn = QPushButton(f"{pct}%")
            btn.clicked.connect(lambda checked=False, p=pct: self.preset.emit(f"op{p}"))
            row.addWidget(btn)
            self._preset_btns.append(btn)
        self.mid_btn = QPushButton(tr("ov.fifty"))
        self.mid_btn.clicked.connect(lambda: self.preset.emit("mid"))
        row.addWidget(self.mid_btn)
        self.reset_op = QPushButton(tr("ov.reset_opacity"))
        self.reset_op.clicked.connect(lambda: self.preset.emit("reset_op"))
        row.addWidget(self.reset_op)
        self.close_btn = QPushButton(tr("ov.off"))
        self.close_btn.clicked.connect(self.closed.emit)
        row.addWidget(self.close_btn)

    def _slide(self, value: int) -> None:
        self.pct.setText(f"{value}%")
        self.opacityChanged.emit(value)

    def set_opacity(self, percent: int) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(int(percent))
        self.pct.setText(f"{int(percent)}%")
        self.slider.blockSignals(False)

    def set_direction(self, mode: OverlayMode) -> None:
        self.dir_a.setChecked(mode == OverlayMode.A_OVER_B)
        self.dir_b.setChecked(mode in (OverlayMode.B_OVER_A, OverlayMode.OVERLAY))

    def retranslate(self) -> None:
        self.title.setText(tr("ov.title"))
        self.dir_a.setText(tr("ov.a_over_b"))
        self.dir_b.setText(tr("ov.b_over_a"))
        self.op_lab.setText(tr("ov.opacity"))
        self.mid_btn.setText(tr("ov.fifty"))
        self.reset_op.setText(tr("ov.reset_opacity"))
        self.close_btn.setText(tr("ov.off"))
