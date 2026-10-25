"""Selectable, copyable geometry analysis log."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFont, QTextCharFormat, QColor, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from core.analysis import format_log, ComparisonResult


class LogWindow(QDialog):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        from app.i18n import tr

        self.setWindowTitle(tr("log.title"))
        self.resize(820, 640)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.filter = QComboBox()
        for text, key in (
            (tr("log.all"), "all"),
            (tr("log.matches"), "match"),
            (tr("log.mismatches"), "mismatch"),
            (tr("log.excluded"), "excluded"),
        ):
            self.filter.addItem(text, key)
        self.sort = QComboBox()
        for text, key in (
            ("Error descending", "err_desc"),
            ("Error ascending", "err_asc"),
            ("ID", "id"),
            ("Group", "group"),
        ):
            self.sort.addItem(text, key)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("log.search"))
        row.addWidget(QLabel(tr("log.show")))
        row.addWidget(self.filter)
        row.addWidget(QLabel(tr("log.sort")))
        row.addWidget(self.sort)
        row.addWidget(self.search, 1)
        layout.addLayout(row)
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(QFont("Consolas", 10))
        self.text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.text, 1)
        btns = QHBoxLayout()
        copy_b = QPushButton(tr("log.copy"))
        save_b = QPushButton(tr("log.save"))
        clear_b = QPushButton(tr("log.clear"))
        close_b = QPushButton(tr("log.close"))
        copy_b.clicked.connect(self.copy_all)
        save_b.clicked.connect(self.save_log)
        clear_b.clicked.connect(self.text.clear)
        close_b.clicked.connect(self.accept)
        for b in (copy_b, save_b, clear_b, close_b):
            btns.addWidget(b)
        layout.addLayout(btns)
        self.filter.currentIndexChanged.connect(self.refresh)
        self.sort.currentIndexChanged.connect(self.refresh)
        self.search.textChanged.connect(self.refresh)
        self.text.mouseDoubleClickEvent = self._dbl  # type: ignore
        self.refresh()

    def _result(self) -> ComparisonResult | None:
        data = self.controller.data.analysis_result
        return ComparisonResult.from_dict(data)

    def refresh(self) -> None:
        result = self._result()
        if result is None:
            self.text.setPlainText("No analysis yet. Press CALCULATE first.")
            return
        if self.controller.analysis_is_stale():
            result.stale = True
        filt = self.filter.currentData()
        sort = self.sort.currentData()
        pts = list(result.points)
        if filt == "match":
            pts = [p for p in pts if p.status == "MATCH"]
        elif filt == "mismatch":
            pts = [p for p in pts if p.status == "MISMATCH"]
        elif filt == "excluded":
            pts = [p for p in pts if not p.included]
        if sort == "err_desc":
            pts.sort(key=lambda p: p.error, reverse=True)
        elif sort == "err_asc":
            pts.sort(key=lambda p: p.error)
        elif sort == "id":
            pts.sort(key=lambda p: p.id)
        elif sort == "group":
            pts.sort(key=lambda p: (p.group, p.id))
        result.points = pts
        text = format_log(result)
        if result.stale:
            text = ">>> MEASUREMENTS CHANGED — RECALCULATE (result is OUT OF DATE) <<<\n\n" + text
        q = self.search.text().strip()
        if q:
            kept = [ln for ln in text.splitlines() if q.lower() in ln.lower()]
            # keep header + matching blocks: simpler to highlight via find
        self.text.setPlainText(text)
        self._highlight_mismatches()
        if q:
            self.text.find(q)

    def _highlight_mismatches(self) -> None:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#e74c3c"))
        doc = self.text.document()
        cursor = self.text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        while True:
            cursor = doc.find("STATUS = MISMATCH", cursor.position())
            if cursor.isNull():
                break
            cursor.mergeCharFormat(fmt)

    def copy_all(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self.text.toPlainText())

    def save_log(self) -> None:
        path, chosen = QFileDialog.getSaveFileName(
            self, "Save log", "analysis_log.txt", "Text (*.txt);;CSV (*.csv);;JSON (*.json)"
        )
        if not path:
            return
        result = self._result()
        dest = Path(path)
        if dest.suffix.lower() == ".csv":
            from exporters.csv_export import export_rows

            rows = [p.as_dict() for p in (result.points if result else [])]
            if not rows:
                QMessageBox.warning(self, "Save log", "No point rows to write.")
                return
            export_rows(dest, list(rows[0].keys()), rows, metadata={"content": "analysis_points"})
        elif dest.suffix.lower() == ".json":
            import json

            dest.write_text(json.dumps(result.as_dict() if result else {}, indent=2), encoding="utf-8")
        else:
            dest.write_text(self.text.toPlainText(), encoding="utf-8")

    def _dbl(self, event) -> None:
        cursor = self.text.cursorForPosition(event.pos())
        cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        word = cursor.selectedText().strip()
        if word and hasattr(self.parent(), "_zoom_to_id"):
            self.parent()._zoom_to_id(word)
        QTextEdit.mouseDoubleClickEvent(self.text, event)
