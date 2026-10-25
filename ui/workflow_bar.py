"""Always-visible 5-step Basic Mode indicator: ✓ completed  ● current  ○ pending."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

STEPS = ("LOAD", "ZERO", "SCALE", "CONTROL", "RESULTS")


class WorkflowBar(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("workflowBar")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        self._labels: list[QLabel] = []
        for i, text in enumerate(STEPS, start=1):
            lab = QLabel(f"○ {i} {text}")
            lab.setStyleSheet("color:#888; padding:2px 8px;")
            layout.addWidget(lab)
            self._labels.append(lab)
        self.lock = QLabel("")
        self.lock.setStyleSheet("padding:2px 10px; font-weight:700;")
        layout.addStretch()
        layout.addWidget(self.lock)

    def set_step(self, step: int, *, locked: bool = False, extra: str = "") -> None:
        for i, (lab, name) in enumerate(zip(self._labels, STEPS), start=1):
            if i < step:
                mark, style = "✓", "color:#2ecc71; padding:2px 8px;"
            elif i == step:
                mark, style = "●", "color:#f0f0f0; background:#1b4f72; padding:2px 8px; border-radius:3px;"
            else:
                mark, style = "○", "color:#777; padding:2px 8px;"
            lab.setText(f"{mark} {i} {name}")
            lab.setStyleSheet(style)
        if locked:
            self.lock.setText("Alignment LOCKED")
            self.lock.setStyleSheet("color:#fff; background:#922b21; padding:2px 10px; font-weight:700;")
        else:
            self.lock.setText(extra)
            self.lock.setStyleSheet("color:#bbb; padding:2px 10px;")
