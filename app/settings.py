from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QSettings


APP_ORG = "PCBTools"
APP_NAME = "PCBImageComparator"


def qt_settings() -> QSettings:
    return QSettings(APP_ORG, APP_NAME)


def app_data_dir() -> Path:
    from PySide6.QtCore import QStandardPaths

    root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    root.mkdir(parents=True, exist_ok=True)
    return root


def recovery_path() -> Path:
    return app_data_dir() / "recovery.picmp"


def tolerance_profiles_path() -> Path:
    return app_data_dir() / "tolerance_profiles.json"


def load_tolerance_profiles() -> list[dict]:
    p = tolerance_profiles_path()
    if not p.is_file():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def save_tolerance_profiles(profiles: list[dict]) -> None:
    tolerance_profiles_path().write_text(json.dumps(profiles, indent=2), encoding="utf-8")
