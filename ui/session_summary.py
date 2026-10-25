from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from models.enums import TransformStatus


class SessionSummary(QWidget):
    navigate = Signal(str)

    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.itemClicked.connect(self._click)
        layout.addWidget(self.list)
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        d = self.controller.data
        rec = d.current_transform
        status = rec.status.value if rec else "NONE"
        self._add("REGISTRATION", f"Status {status}", "transform")
        a = "calibrated" if d.image_a and d.image_a.calibration.is_calibrated else "uncalibrated"
        b = "calibrated" if d.image_b and d.image_b.calibration.is_calibrated else "uncalibrated"
        self._add("CALIBRATION", f"A {a} / B {b}", "calibration")
        refs = self.controller.enabled_references()
        rmse = rec.summary.get("rmse") if rec else None
        self._add("REFERENCE QUALITY", f"{len(refs)} refs  RMSE={rmse}", "points")
        comps = self.controller.group_comparisons()
        for c in comps:
            self._add("GROUP RESULTS", f"{c.name}: {c.internal_state.value}", f"group:{c.name}")
        self._add("CONTOUR RESULTS", f"{len(d.contours)} contours", "contours")
        unverified = [lm.id for lm in d.landmarks if lm.confidence.value in ("UNVERIFIED", "CROPPED", "INFERRED")]
        self._add("UNVERIFIED ITEMS", ", ".join(unverified) or "none", "points")
        if d.lens_distortion == "UNKNOWN":
            self._add("LENS", "Lens distortion UNKNOWN (no camera profile)", "calibration")

    def _add(self, section: str, text: str, target: str) -> None:
        item = QListWidgetItem(f"{section}: {text}")
        item.setData(32, target)
        self.list.addItem(item)

    def _click(self, item: QListWidgetItem) -> None:
        self.navigate.emit(str(item.data(32)))
