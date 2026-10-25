"""Accept alignment with an explicit orientation preview. Nothing is auto-chosen."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
)

from core.orientation import Orientation
from models.enums import OrientationName, Side


PRESETS = (
    ("Normal", OrientationName.NORMAL),
    ("Mirror X", OrientationName.MIRROR_X),
    ("Mirror Y", OrientationName.MIRROR_Y),
    ("Rotate 180°", OrientationName.ROTATE_180),
)


class AcceptPreviewDialog(QDialog):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Accept alignment — choose orientation")
        self._saved = None
        b = controller.image(Side.B)
        if b is not None:
            self._saved = b.orientation
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Choose the orientation that makes corresponding features match.\n"
                "This is NOT chosen automatically from the lowest error.\n"
                "DISPLAY mirror (view only) is separate — this changes REGISTRATION orientation of B."
            )
        )
        self.radios: list[tuple[QRadioButton, OrientationName]] = []
        current = b.orientation.name if b else OrientationName.NORMAL
        for text, name in PRESETS:
            rb = QRadioButton(text)
            rb.setChecked(name == current)
            rb.toggled.connect(self._preview)
            layout.addWidget(rb)
            self.radios.append((rb, name))
        self.info = QLabel("")
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self._cancel)
        layout.addWidget(btns)
        self._preview()

    def _selected(self) -> OrientationName:
        for rb, name in self.radios:
            if rb.isChecked():
                return name
        return OrientationName.NORMAL

    def _preview(self, *_args) -> None:
        name = self._selected()
        doc = self.controller.image(Side.B)
        if doc is None:
            self.info.setText("Image B is not loaded.")
            return
        if self.controller.is_locked():
            self.info.setText("Alignment is LOCKED. Unlock before changing registration orientation.")
            return
        doc.orientation = Orientation.from_name(name)
        rec = self.controller.compute_from_zero_scale()
        if rec is None:
            self.info.setText("Set ZERO and SCALE on both images first.")
            return
        t = rec.transform
        self.info.setText(
            f"PREVIEW (not accepted)\n"
            f"Orientation B: {doc.orientation.status_label()}\n"
            f"Scale A→B: {t.scale:.4f}×\n"
            f"Rotation: {t.rotation_deg:.3f}°\n"
            f"Translation: {t.tx:.2f}, {t.ty:.2f} px"
        )
        self.controller.imagesChanged.emit()

    def _accept(self) -> None:
        rec = self.controller.data.current_transform
        if rec is None:
            rec = self.controller.compute_from_zero_scale()
        if rec is None:
            return
        self.controller.accept_transform()
        self.accept()

    def _cancel(self) -> None:
        doc = self.controller.image(Side.B)
        if doc is not None and self._saved is not None:
            doc.orientation = self._saved
            self.controller.compute_from_zero_scale()
            self.controller.imagesChanged.emit()
        self.reject()
