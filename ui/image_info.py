"""Image information — confirms original source resolution was not reduced."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QTextEdit, QVBoxLayout

from models.enums import Side


class ImageInfoDialog(QDialog):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Image Information")
        self.resize(520, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Source image metadata. Display zoom never changes these values."))
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(_format_info(controller))
        layout.addWidget(text, 1)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        layout.addWidget(btns)


def _block(controller, side: Side) -> str:
    info = controller.image_source_info(side)
    if not info:
        return f"MODEL {side.value}\n  (not loaded)\n"
    mm = info.get("mm_per_px")
    cal = (
        f"{mm:.6f} mm/px  ({mm * 1000:.4f} µm/px)" if mm else "not calibrated"
    )
    return (
        f"MODEL {side.value}\n"
        f"  Format: {info.get('format') or 'unknown'}\n"
        f"  Original width: {info.get('width')} px\n"
        f"  Original height: {info.get('height')} px\n"
        f"  Color mode: {info.get('mode') or '—'}\n"
        f"  Source file size: {info.get('file_size') or 0} bytes\n"
        f"  Original bytes preserved: {'yes' if info.get('has_original_bytes') else 'no'}\n"
        f"  Path: {info.get('source_path') or 'embedded / clipboard'}\n"
        f"  Calibration: {cal}\n"
    )


def _format_info(controller) -> str:
    return _block(controller, Side.A) + "\n" + _block(controller, Side.B)
