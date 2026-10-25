"""One application-wide undo router.

Ctrl+Z / Ctrl+Y go to the project QUndoStack unless the user is typing
in an editable text field. Viewers, tables, docks, sliders and toolbars
must not swallow project undo.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QTextEdit


def is_live_text_editor(widget) -> bool:
    if widget is None:
        return False
    if isinstance(widget, QLineEdit) and not widget.isReadOnly():
        return True
    if isinstance(widget, (QPlainTextEdit, QTextEdit)) and not widget.isReadOnly():
        return True
    return False


class ProjectUndoFilter(QObject):
    def __init__(self, stack, parent=None) -> None:
        super().__init__(parent)
        self.stack = stack

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.KeyPress:
            return False
        key = event
        undo = key.matches(QKeySequence.StandardKey.Undo)
        redo = key.matches(QKeySequence.StandardKey.Redo)
        if not redo and key.modifiers() & Qt.KeyboardModifier.ControlModifier and key.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            if key.key() == Qt.Key.Key_Z:
                redo = True
        if not undo and not redo:
            return False
        if is_live_text_editor(QApplication.focusWidget()) or is_live_text_editor(obj):
            return False
        if undo:
            if self.stack.canUndo():
                self.stack.undo()
            return True
        if self.stack.canRedo():
            self.stack.redo()
        return True
