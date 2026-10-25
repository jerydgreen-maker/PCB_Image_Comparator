from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit, QVBoxLayout


class AuditDialog(QDialog):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Audit log")
        self.resize(640, 400)
        layout = QVBoxLayout(self)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        lines = [f"{e.timestamp}  {e.action}  {e.detail}" for e in controller.data.audit] or ["(empty)"]
        text.setPlainText("\n".join(lines))
        layout.addWidget(text)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        layout.addWidget(btns)
