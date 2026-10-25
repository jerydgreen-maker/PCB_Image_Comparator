from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from models.enums import Side


class PerspectiveDialog(QDialog):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Perspective correction — produces a DERIVED image")
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Optional preprocessing. The original file is never overwritten. "
                "The corrected image is DERIVED. This is not applied automatically during measurement."
            )
        )
        form = QFormLayout()
        self.side = QLineEdit("A")
        self.pts = QLineEdit("0,0  1000,0  1000,800  0,800")
        self.width = QLineEdit("1000")
        self.height = QLineEdit("800")
        form.addRow("Side (A/B)", self.side)
        form.addRow("4 source corners (x,y x,y x,y x,y)", self.pts)
        form.addRow("Output width px", self.width)
        form.addRow("Output height px", self.height)
        layout.addLayout(form)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._apply)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _apply(self) -> None:
        import cv2

        side = Side.A if self.side.text().strip().upper() == "A" else Side.B
        img = self.controller.image(side)
        if img is None or img.array is None:
            self.controller._err(f"Image {side.value} is not loaded.")
            return
        nums = [float(x) for x in self.pts.text().replace(",", " ").split()]
        if len(nums) != 8:
            self.controller._err("Enter exactly 4 source corners.")
            return
        src = np.array(nums, dtype=np.float32).reshape(4, 2)
        w, h = int(self.width.text()), int(self.height.text())
        dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
        H = cv2.getPerspectiveTransform(src, dst)
        derived = cv2.warpPerspective(img.array, H, (w, h))
        dest, _ = QFileDialog.getSaveFileName(self, "Save derived image (original untouched)", "derived.png", "PNG (*.png)")
        if not dest:
            return
        from exporters.image_export import save_rgb
        from importers.image_import import array_to_rgb

        save_rgb(dest, array_to_rgb(derived))
        self.controller.load_image(
            side,
            dest,
            derived=True,
            derivation={"type": "perspective", "homography": H.tolist(), "source_corners": src.tolist()},
        )
        self.accept()
