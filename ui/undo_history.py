"""Edit > Undo History — numbered user operations from the project QUndoStack."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QListWidget, QUndoView, QVBoxLayout


class UndoHistoryDialog(QDialog):
    def __init__(self, stack, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Undo History")
        self.resize(420, 480)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("User operations. Click an entry to jump in the undo stack."))
        numbered = QListWidget()
        for i in range(stack.count()):
            cmd = stack.command(i)
            text = cmd.text() if cmd is not None else ""
            numbered.addItem(f"{i + 1:03d} {text}")
        if stack.count() == 0:
            numbered.addItem("(no undoable project commands)")
        cur = stack.index()
        if 0 < cur <= numbered.count():
            numbered.setCurrentRow(cur - 1)
        numbered.currentRowChanged.connect(lambda row: stack.setIndex(row + 1) if row >= 0 else None)
        layout.addWidget(numbered)
        view = QUndoView(stack)
        view.setEmptyLabel("(no undoable project commands)")
        layout.addWidget(view, 1)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        layout.addWidget(btns)
