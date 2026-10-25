from __future__ import annotations

from PySide6.QtWidgets import QLabel, QListWidget, QVBoxLayout, QWidget

from models.enums import TransformStatus


class TransformInspector(QWidget):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        layout = QVBoxLayout(self)
        self.lock_banner = QLabel("")
        self.lock_banner.setStyleSheet("font-weight:700; padding:6px;")
        self.body = QLabel("No transform.")
        self.body.setWordWrap(True)
        self.history = QListWidget()
        layout.addWidget(self.lock_banner)
        layout.addWidget(self.body)
        layout.addWidget(QLabel("Accepted transform history"))
        layout.addWidget(self.history)
        self.refresh()

    def refresh(self) -> None:
        rec = self.controller.data.current_transform
        if rec is None:
            self.lock_banner.setText("NO TRANSFORM")
            self.lock_banner.setStyleSheet("background:#7f8c8d; color:white; font-weight:700; padding:6px;")
            self.body.setText("Place reference points, then Review Transform.")
            return
        if rec.status == TransformStatus.LOCKED:
            self.lock_banner.setText("ALIGNMENT LOCKED")
            self.lock_banner.setStyleSheet("background:#c0392b; color:white; font-weight:700; padding:8px;")
        elif rec.status == TransformStatus.ACCEPTED:
            self.lock_banner.setText("TRANSFORM ACCEPTED")
            self.lock_banner.setStyleSheet("background:#27ae60; color:white; font-weight:700; padding:6px;")
        else:
            self.lock_banner.setText("PROVISIONAL — not yet accepted")
            self.lock_banner.setStyleSheet("background:#d35400; color:white; font-weight:700; padding:6px;")
        t = rec.transform
        m = t.matrix()
        self.body.setText(
            f"Status: {rec.status.value}<br/>"
            f"Mode: {rec.mode.value}<br/>"
            f"Orientation B: {rec.orientation_b.name.value}<br/>"
            f"Mirror X: {rec.orientation_b.mirror_x}  Mirror Y: {rec.orientation_b.mirror_y}<br/>"
            f"Scale: {t.scale:.6f}<br/>"
            f"Rotation: {t.rotation_deg:.4f}°<br/>"
            f"TX: {t.tx:.4f} px<br/>"
            f"TY: {t.ty:.4f} px<br/>"
            f"Matrix:<br/>"
            f"[{m[0,0]:.5f} {m[0,1]:.5f} {m[0,2]:.5f}]<br/>"
            f"[{m[1,0]:.5f} {m[1,1]:.5f} {m[1,2]:.5f}]<br/>"
            f"[{m[2,0]:.5f} {m[2,1]:.5f} {m[2,2]:.5f}]"
        )
        self.history.clear()
        for i, h in enumerate(self.controller.data.transform_history, start=1):
            self.history.addItem(
                f"Transform {i}: {h.mode.value} scale={h.transform.scale:.5f} "
                f"rot={h.transform.rotation_deg:.3f}° {h.created_at}"
            )
