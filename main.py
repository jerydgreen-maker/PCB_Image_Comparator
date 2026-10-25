"""PCB Image Comparator — desktop metrology / image-registration tool."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

def _bundle_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


ROOT = _bundle_dir()
if not getattr(sys, "frozen", False) and str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOGO_PATH = ROOT / "logo.jpg"


def _load_app_icon():
    from PySide6.QtGui import QIcon

    if not LOGO_PATH.is_file():
        return None
    icon = QIcon(str(LOGO_PATH))
    return icon if not icon.isNull() else None


def _set_windows_taskbar_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PCBTools.PCBImageComparator.1")
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PCB Image Comparator")
    parser.add_argument("project", nargs="?", help="Optional .picmp project to open")
    parser.add_argument("--offscreen", action="store_true", help="Use offscreen Qt platform (tests)")
    args = parser.parse_args(argv)

    if args.offscreen:
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    _set_windows_taskbar_app_id()

    from PySide6.QtWidgets import QApplication

    from app.main_window import MainWindow
    from app.settings import qt_settings
    from ui.theme import apply_dark, apply_light

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("PCB Image Comparator")
    app.setOrganizationName("PCBTools")
    app_icon = _load_app_icon()
    if app_icon is not None:
        app.setWindowIcon(app_icon)
    try:
        app.setStyle("Fusion")
    except Exception:
        pass
    theme = str(qt_settings().value("theme", "dark")).lower()
    if theme == "light":
        apply_light(app)
    else:
        apply_dark(app)
    win = MainWindow()
    if app_icon is not None:
        win.setWindowIcon(app_icon)
    if args.project:
        win.controller.load(args.project)
        win.refresh_all()
    win.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
