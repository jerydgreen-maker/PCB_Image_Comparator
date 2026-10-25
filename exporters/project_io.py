"""ZIP+JSON project format (*.picmp). Transparent, documented, not opaque binary."""

from __future__ import annotations

import json
import os
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from importers.image_import import encode_png, load_image_array, load_image_bytes
from models.document import ProjectData

FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
PROJECT_NAME = "project.json"
AUDIT_NAME = "audit.json"


class ProjectIOError(ValueError):
    pass


def _write_image_into_zip(zf: zipfile.ZipFile, doc, key: str) -> str | None:
    if doc is None:
        return None
    if doc.original_bytes:
        ext = _ext_for_format(doc.original_format, doc.source_path)
        name = f"images/{key}{ext}"
        zf.writestr(name, doc.original_bytes)
        doc.embedded_name = name
        return name
    if doc.source_path and os.path.isfile(doc.source_path):
        ext = Path(doc.source_path).suffix.lower() or ".png"
        name = f"images/{key}{ext}"
        raw = Path(doc.source_path).read_bytes()
        zf.writestr(name, raw)
        doc.original_bytes = raw
        doc.embedded_name = name
        return name
    if doc.array is not None:
        name = f"images/{key}.png"
        zf.writestr(name, encode_png(doc.array))
        doc.embedded_name = name
        return name
    return None


def _ext_for_format(fmt: str, source_path: str | None) -> str:
    if source_path:
        ext = Path(source_path).suffix.lower()
        if ext:
            return ext
    table = {"jpeg": ".jpg", "jpg": ".jpg", "png": ".png", "tiff": ".tif", "bmp": ".bmp", "webp": ".webp"}
    return table.get((fmt or "").lower(), ".png")


def save_project(project: ProjectData, path: str | Path, *, embed_images: bool = True) -> None:
    path = Path(path)
    project.modified_at = datetime.now(timezone.utc).isoformat()
    tmp = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if embed_images:
            _write_image_into_zip(zf, project.image_a, "a")
            _write_image_into_zip(zf, project.image_b, "b")
        payload = project.as_dict()
        zf.writestr(PROJECT_NAME, json.dumps(payload, indent=2))
        zf.writestr(
            MANIFEST_NAME,
            json.dumps(
                {
                    "format": "picmp",
                    "format_version": FORMAT_VERSION,
                    "application": "PCB Image Comparator",
                    "name": project.name,
                    "created_at": project.created_at,
                    "modified_at": project.modified_at,
                    "files": [PROJECT_NAME, AUDIT_NAME],
                },
                indent=2,
            ),
        )
        zf.writestr(AUDIT_NAME, json.dumps([a.as_dict() for a in project.audit], indent=2))
    os.replace(tmp, path)


def load_project(path: str | Path) -> ProjectData:
    path = Path(path)
    if not path.is_file():
        raise ProjectIOError(f"Project file not found: {path}")
    with zipfile.ZipFile(path, "r") as zf:
        names = set(zf.namelist())
        if PROJECT_NAME not in names:
            raise ProjectIOError("Invalid .picmp file: missing project.json")
        data = json.loads(zf.read(PROJECT_NAME).decode("utf-8"))
        project = ProjectData.from_dict(data)
        for doc, key in ((project.image_a, "a"), (project.image_b, "b")):
            if doc is None:
                continue
            embed = doc.embedded_name
            if embed and embed in names:
                raw = zf.read(embed)
                arr, w, h = load_image_bytes(raw)
                doc.array = arr
                doc.original_bytes = raw
                doc.source_byte_count = len(raw)
                doc.width = w
                doc.height = h
            elif doc.source_path and os.path.isfile(doc.source_path):
                arr, w, h = load_image_array(doc.source_path)
                doc.array = arr
                doc.width = w
                doc.height = h
        return project


def save_recovery(project: ProjectData, recovery_path: str | Path) -> None:
    recovery_path = Path(recovery_path)
    recovery_path.parent.mkdir(parents=True, exist_ok=True)
    save_project(project, recovery_path, embed_images=True)
