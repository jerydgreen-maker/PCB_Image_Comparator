from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from core.statistics import summarize
from models.units import px_to_unit


class MeasurementPanel(QWidget):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        layout = QVBoxLayout(self)
        self.body = QLabel("No measurements.")
        self.body.setWordWrap(True)
        layout.addWidget(self.body)
        layout.addStretch()

    def refresh(self) -> None:
        res = self.controller.measurement_residuals(confirmed_only=not self.controller.data.include_unverified_in_stats)
        unit = self.controller.data.display_unit
        cal = self.controller.data.image_b.calibration.mm_per_px if self.controller.data.image_b else None
        if not res:
            self.body.setText("No measurement residuals yet. Lock alignment, click A, then actual on B.")
            return
        stats = summarize([r.distance for r in res])
        lines = [
            f"Count: {stats.count}",
            f"Mean: {_u(stats.mean, cal, unit)}",
            f"Median: {_u(stats.median, cal, unit)}",
            f"Std: {_u(stats.std, cal, unit)}",
            f"RMSE: {_u(stats.rmse, cal, unit)}",
            f"Min: {_u(stats.minimum, cal, unit)}",
            f"Max: {_u(stats.maximum, cal, unit)}",
            "",
            "Individual errors:",
        ]
        for r in res:
            lines.append(
                f"{r.point_id}: DX={_u(r.dx, cal, unit)} DY={_u(r.dy, cal, unit)} "
                f"ERR={_u(r.distance, cal, unit)}"
            )
        findings = self.controller.run_diagnostics()
        if findings:
            lines.append("")
            lines.append("Diagnostics (not auto-fixed):")
            for f in findings:
                lines.append(f.flag.value + " — " + f.message)
        self.body.setText("<br/>".join(lines))


def _u(v, cal, unit):
    if v is None:
        return "—"
    phys = px_to_unit(v, cal, unit)
    if phys is None:
        return f"{v:.4f} px"
    return f"{phys:.4f} {unit.value}"
