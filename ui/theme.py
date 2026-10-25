"""Dark / light Fusion palettes. Buttons stay readable — never white-on-white."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


DARK_QSS = """
QWidget { color: #e6e6e6; background: #2b2b2b; }
QMainWindow, QDialog, QDockWidget, QMenuBar, QMenu, QStatusBar, QToolBar {
    background: #2b2b2b; color: #e6e6e6;
}
QMenuBar::item:selected, QMenu::item:selected { background: #3d5a80; }
QMenu::item:disabled { color: #777; }
QToolBar { border: none; spacing: 4px; padding: 2px; }
QToolButton, QPushButton {
    background: #3a3a3a;
    color: #f0f0f0;
    border: 1px solid #666;
    border-radius: 3px;
    padding: 4px 10px;
}
QToolButton:hover, QPushButton:hover { background: #4a5560; border-color: #88a; }
QToolButton:pressed, QPushButton:pressed { background: #2d3a48; }
QToolButton:checked { background: #1b4f72; border-color: #5dade2; }
QToolButton:disabled, QPushButton:disabled { color: #777; background: #333; border-color: #444; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {
    background: #1e1e1e; color: #eee; border: 1px solid #666; padding: 2px;
}
QTableWidget, QTableView, QListWidget {
    background: #1e1e1e; color: #eee; gridline-color: #444;
    alternate-background-color: #262626;
}
QHeaderView::section { background: #3a3a3a; color: #eee; border: 1px solid #555; padding: 3px; }
QScrollArea { background: #2b2b2b; border: none; }
QSplitter::handle { background: #444; }
QLabel { color: #e6e6e6; background: transparent; }
QCheckBox, QRadioButton { color: #e6e6e6; }
QStatusBar QLabel { color: #ddd; }
QDockWidget::title { background: #333; padding: 4px; }
#viewerHeader { background: #1f1f1f; }
#viewerHeader QLabel { color: #eee; }
#emptyViewer { background: #1a1a1a; }
#emptyViewer QLabel { color: #c8c8c8; background: transparent; }
#workflowBar { background: #242424; }
"""


def apply_dark(app: QApplication) -> None:
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(43, 43, 43))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(230, 230, 230))
    pal.setColor(QPalette.ColorRole.Base, QColor(30, 30, 30))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(38, 38, 38))
    pal.setColor(QPalette.ColorRole.Text, QColor(230, 230, 230))
    pal.setColor(QPalette.ColorRole.Button, QColor(58, 58, 58))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(240, 240, 240))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(61, 90, 128))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(40, 40, 40))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(230, 230, 230))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(140, 140, 140))
    disabled = QColor(120, 120, 120)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, disabled)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, disabled)
    app.setPalette(pal)
    app.setStyleSheet(DARK_QSS)


def apply_light(app: QApplication) -> None:
    app.setPalette(app.style().standardPalette())
    app.setStyleSheet("")
