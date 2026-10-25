"""Viewer frame: header + dark empty-state drop target + image canvas."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from models.enums import Side
from ui.image_view import ImageView


class ViewerPane(QWidget):
    openRequested = Signal()
    pasteRequested = Signal()
    replaceRequested = Signal()
    removeRequested = Signal()
    filesDropped = Signal(list)
    imageDropped = Signal(object)

    def __init__(self, side: Side, view: ImageView, parent=None) -> None:
        super().__init__(parent)
        self.side = side
        self.view = view
        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("viewerHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(8, 4, 8, 4)
        self.title = QLabel(f"MODEL {side.value}")
        self.title.setStyleSheet("font-weight: 700;")
        self.orient = QLabel("NORMAL")
        self.orient.setToolTip("REGISTRATION orientation (work coordinates). Display-only mirror is listed separately.")
        self.name = QLabel("No image")
        self.replace_btn = QPushButton("Replace")
        self.remove_btn = QPushButton("X")
        self.replace_btn.setToolTip(f"Replace image {side.value}")
        self.remove_btn.setToolTip(f"Remove image {side.value}")
        self.replace_btn.setVisible(False)
        self.remove_btn.setVisible(False)
        open_btn = QPushButton("Open")
        paste_btn = QPushButton("Paste")
        open_btn.setToolTip(f"Open image {side.value}")
        paste_btn.setToolTip(f"Paste screenshot into {side.value} (Ctrl+V)")
        open_btn.clicked.connect(self.openRequested.emit)
        paste_btn.clicked.connect(self.pasteRequested.emit)
        self.replace_btn.clicked.connect(self.replaceRequested.emit)
        self.remove_btn.clicked.connect(self.removeRequested.emit)
        row.addWidget(self.title)
        row.addWidget(self.orient)
        row.addWidget(self.name, 1)
        row.addWidget(open_btn)
        row.addWidget(paste_btn)
        row.addWidget(self.replace_btn)
        row.addWidget(self.remove_btn)
        layout.addWidget(header)

        self.stack = QStackedWidget()
        self.empty = _EmptyState(side)
        self.empty.openRequested.connect(self.openRequested.emit)
        self.empty.pasteRequested.connect(self.pasteRequested.emit)
        self.stack.addWidget(self.empty)
        self.stack.addWidget(view)
        layout.addWidget(self.stack, 1)
        self.show_empty()

    def show_empty(self) -> None:
        self.stack.setCurrentWidget(self.empty)
        self.name.setText("No image")
        self.orient.setText("NORMAL")
        self.replace_btn.setVisible(False)
        self.remove_btn.setVisible(False)

    def show_image(self, caption: str) -> None:
        self.stack.setCurrentWidget(self.view)
        self.name.setText(caption)
        self.replace_btn.setVisible(True)
        self.remove_btn.setVisible(True)

    def set_orientation_text(self, text: str) -> None:
        self.orient.setText(text)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls() or event.mimeData().hasImage():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        mime = event.mimeData()
        paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()
            return
        if mime.hasImage():
            self.imageDropped.emit(mime.imageData())
            event.acceptProposedAction()


class _EmptyState(QWidget):
    openRequested = Signal()
    pasteRequested = Signal()

    def __init__(self, side: Side) -> None:
        super().__init__()
        self.setObjectName("emptyViewer")
        self.setAcceptDrops(True)
        box = QVBoxLayout(self)
        box.addStretch()
        title = QLabel(f"MODEL {side.value}")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        hint = QLabel("Drop image here\nor Open Image\nor Ctrl+V to paste screenshot")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row = QHBoxLayout()
        row.addStretch()
        open_btn = QPushButton("Open Image")
        paste_btn = QPushButton("Paste")
        open_btn.clicked.connect(self.openRequested.emit)
        paste_btn.clicked.connect(self.pasteRequested.emit)
        row.addWidget(open_btn)
        row.addWidget(paste_btn)
        row.addStretch()
        box.addWidget(title)
        box.addWidget(hint)
        box.addLayout(row)
        box.addStretch()
