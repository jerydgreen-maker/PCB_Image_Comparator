"""Non-destructive IMAGE ENHANCEMENT. Never writes source pixels."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from app.i18n import tr
from core.super_resolution import super_resolution_status
from models.adjustments import PRESETS
from models.enums import Side


class _SideAdjust(QGroupBox):
    def __init__(self, controller, side: Side, parent=None) -> None:
        super().__init__(tr("adj.model").format(side=side.value), parent)
        self.controller = controller
        self.side = side
        self._syncing = False
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(40)
        self._pending: list[tuple[str, float]] = []
        self._debounce.timeout.connect(self._flush)

        form = QFormLayout(self)
        self.mode = QComboBox()
        for key, mid in (("adj.off", "OFF"), ("adj.manual", "MANUAL"), ("adj.auto", "AUTO"), ("adj.advanced", "ADVANCED")):
            self.mode.addItem(tr(key), mid)
        self.mode.currentIndexChanged.connect(self._mode)
        form.addRow(tr("adj.mode"), self.mode)

        self.sliders: dict[str, QSlider] = {}
        self.labels: dict[str, QLabel] = {}
        for field, lo, hi, tip_key in (
            ("brightness", -100, 100, "adj.brightness"),
            ("contrast", -100, 100, "adj.contrast"),
            ("sharpness", -100, 100, "adj.sharpness"),
            ("clarity", 0, 100, "adj.clarity"),
            ("denoise", 0, 100, "adj.denoise"),
            ("highlights", -100, 100, "adj.highlights"),
            ("shadows", -100, 100, "adj.shadows"),
            ("saturation", -100, 100, "adj.saturation"),
            ("exposure", -100, 100, "adj.exposure"),
        ):
            row = QHBoxLayout()
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(lo, hi)
            slider.setToolTip(tr(tip_key))
            num = QLabel("0")
            num.setMinimumWidth(36)
            slider.valueChanged.connect(lambda v, f=field, n=num: self._slide(f, v, n))
            row.addWidget(slider, 1)
            row.addWidget(num)
            form.addRow(tr(tip_key), row)
            self.sliders[field] = slider
            self.labels[field] = num

        self.gamma = QDoubleSpinBox()
        self.gamma.setRange(0.20, 5.00)
        self.gamma.setSingleStep(0.05)
        self.gamma.setDecimals(2)
        self.gamma.setValue(1.00)
        self.gamma.valueChanged.connect(lambda v: self._set("gamma", float(v)))
        gamma_row = QHBoxLayout()
        gamma_row.addWidget(self.gamma)
        greset = QPushButton(tr("adj.reset_gamma"))
        greset.clicked.connect(lambda: self.gamma.setValue(1.00))
        gamma_row.addWidget(greset)
        form.addRow(tr("adj.gamma"), gamma_row)

        self.gray = QCheckBox(tr("adj.grayscale"))
        self.clahe = QCheckBox(tr("adj.clahe"))
        self.edge = QCheckBox(tr("adj.edge"))
        self.invert = QCheckBox(tr("adj.invert"))
        self.gray.toggled.connect(lambda v: self._set("grayscale", v, merge=False))
        self.clahe.toggled.connect(lambda v: self._set("clahe", v, merge=False))
        self.edge.toggled.connect(lambda v: self._set("edge_enhance", v, merge=False))
        self.invert.toggled.connect(lambda v: self._set("invert", v, merge=False))
        form.addRow(self.gray)
        form.addRow(self.clahe)
        form.addRow(self.edge)
        form.addRow(self.invert)

        self.preset = QComboBox()
        self.preset.addItems(list(PRESETS.keys()))
        apply_p = QPushButton(tr("adj.apply_preset"))
        apply_p.clicked.connect(self._preset)
        prow = QHBoxLayout()
        prow.addWidget(self.preset, 1)
        prow.addWidget(apply_p)
        form.addRow(tr("adj.preset"), prow)

        cmp = QHBoxLayout()
        self.cmp_btns = {}
        for key, mid in (("adj.cmp_original", "original"), ("adj.cmp_enhanced", "enhanced"), ("adj.cmp_split", "split"), ("adj.cmp_flicker", "flicker")):
            b = QPushButton(tr(key))
            b.setCheckable(True)
            b.clicked.connect(lambda checked=False, m=mid: self._compare(m))
            cmp.addWidget(b)
            self.cmp_btns[mid] = b
        form.addRow(tr("adj.compare"), cmp)

        auto_row = QHBoxLayout()
        preview = QPushButton(tr("adj.preview"))
        apply_view = QPushButton(tr("adj.apply_view"))
        reset = QPushButton(tr("adj.reset_side").format(side=side.value))
        preview.clicked.connect(lambda: self.controller.apply_auto_enhance(side))
        apply_view.clicked.connect(lambda: self.controller.apply_auto_enhance(side))
        reset.clicked.connect(lambda: self.controller.reset_adjustments(side))
        auto_row.addWidget(preview)
        auto_row.addWidget(apply_view)
        auto_row.addWidget(reset)
        form.addRow(auto_row)

        sr = super_resolution_status()
        self.sr_label = QLabel(tr("adj.sr_blocked") if not sr["available"] else sr["label"])
        self.sr_label.setWordWrap(True)
        form.addRow(self.sr_label)

        exp = QPushButton(tr("adj.export_enhanced"))
        exp.clicked.connect(self._export)
        form.addRow(exp)

    def _mode(self) -> None:
        if self._syncing:
            return
        mode = self.mode.currentData()
        self.controller.set_enhance_mode(self.side, mode)
        if mode == "AUTO":
            self.controller.apply_auto_enhance(self.side)

    def _compare(self, mode: str) -> None:
        self.controller.set_enhance_compare(self.side, mode)
        for mid, b in self.cmp_btns.items():
            b.setChecked(mid == mode)

    def _slide(self, field: str, value: int, label: QLabel) -> None:
        label.setText(f"{value:+d}" if value else "0")
        if self._syncing:
            return
        doc = self.controller.image(self.side)
        if doc and doc.adjustments.enhance_mode == "OFF":
            self.controller.set_adjustment(self.side, "enhance_mode", "MANUAL", merge=False)
        self._pending = [p for p in self._pending if p[0] != field]
        self._pending.append((field, float(value)))
        self._debounce.start()

    def _flush(self) -> None:
        for field, value in self._pending:
            self.controller.set_adjustment(self.side, field, value, merge=True)
        self._pending.clear()

    def _set(self, field: str, value, *, merge: bool = True) -> None:
        if self._syncing:
            return
        doc = self.controller.image(self.side)
        if doc and doc.adjustments.enhance_mode == "OFF" and field != "enhance_mode":
            self.controller.set_adjustment(self.side, "enhance_mode", "MANUAL", merge=False)
        self.controller.set_adjustment(self.side, field, value, merge=merge)

    def _preset(self) -> None:
        self.controller.apply_adjustment_preset(self.side, self.preset.currentText())

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("adj.export_enhanced"), f"{self.side.value}_enhanced.png", "PNG (*.png)")
        if path:
            self.controller.export_enhanced_png(self.side, path)

    def retranslate(self) -> None:
        self.setTitle(tr("adj.model").format(side=self.side.value))

    def sync_from_model(self) -> None:
        doc = self.controller.image(self.side)
        if doc is None:
            return
        adj = doc.adjustments
        self._syncing = True
        try:
            idx = self.mode.findData(adj.enhance_mode)
            if idx >= 0:
                self.mode.setCurrentIndex(idx)
            for field, slider in self.sliders.items():
                slider.setValue(int(getattr(adj, field)))
                val = int(getattr(adj, field))
                self.labels[field].setText(f"{val:+d}" if val else "0")
            self.gamma.setValue(float(adj.gamma))
            self.gray.setChecked(adj.grayscale)
            self.invert.setChecked(adj.invert)
            self.clahe.setChecked(adj.clahe)
            self.edge.setChecked(adj.edge_enhance)
            for mid, b in self.cmp_btns.items():
                b.setChecked(mid == adj.compare_mode)
        finally:
            self._syncing = False


class ImageAdjustmentsPanel(QWidget):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        layout = QVBoxLayout(self)
        self.banner = QLabel(tr("adj.banner"))
        self.banner.setWordWrap(True)
        layout.addWidget(self.banner)
        self.side_a = _SideAdjust(controller, Side.A)
        self.side_b = _SideAdjust(controller, Side.B)
        layout.addWidget(self.side_a)
        layout.addWidget(self.side_b)
        row = QHBoxLayout()
        a2b = QPushButton(tr("adj.copy_a2b"))
        b2a = QPushButton(tr("adj.copy_b2a"))
        both = QPushButton(tr("adj.reset_both"))
        a2b.clicked.connect(lambda: controller.copy_adjustments(Side.A, Side.B))
        b2a.clicked.connect(lambda: controller.copy_adjustments(Side.B, Side.A))
        both.clicked.connect(lambda: controller.reset_adjustments(None))
        row.addWidget(a2b)
        row.addWidget(b2a)
        row.addWidget(both)
        layout.addLayout(row)
        layout.addStretch(1)
        controller.imagesChanged.connect(self.refresh)
        controller.changed.connect(self.refresh)
        self.refresh()

    def retranslate(self) -> None:
        self.banner.setText(tr("adj.banner"))
        self.side_a.retranslate()
        self.side_b.retranslate()

    def refresh(self) -> None:
        self.side_a.sync_from_model()
        self.side_b.sync_from_model()
