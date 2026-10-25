"""Raster import. Original files are never overwritten."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

RASTER_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}


class ImageImportError(ValueError):
    pass


def inspect_and_decode(data: bytes) -> tuple[np.ndarray, int, int, dict]:
    """Decode a working BGR copy. Caller must keep `data` as the original source bytes."""
    from io import BytesIO

    with Image.open(BytesIO(data)) as img:
        meta = {
            "format": (img.format or "").lower(),
            "mode": img.mode,
            "width": img.size[0],
            "height": img.size[1],
        }
        work = img.convert("RGB") if img.mode not in ("RGB", "L") else img
        if work.mode == "L":
            arr = np.array(work)
            bgr = np.stack([arr, arr, arr], axis=-1)
        else:
            arr = np.array(work.convert("RGB"))
            bgr = arr[:, :, ::-1].copy()
    h, w = bgr.shape[:2]
    meta["decoded_width"] = w
    meta["decoded_height"] = h
    return np.ascontiguousarray(bgr), w, h, meta


def load_image_bytes(data: bytes) -> tuple[np.ndarray, int, int]:
    arr, w, h, _meta = inspect_and_decode(data)
    return arr, w, h


def load_image_array(path: str | Path) -> tuple[np.ndarray, int, int]:
    path = Path(path)
    if not path.is_file():
        raise ImageImportError(f"Image not found: {path}")
    if path.suffix.lower() not in RASTER_EXTENSIONS:
        raise ImageImportError(f"Unsupported raster format: {path.suffix}")
    return load_image_bytes(path.read_bytes())


def load_image_preserving_source(path: str | Path) -> tuple[np.ndarray, int, int, bytes, dict]:
    path = Path(path)
    raw = path.read_bytes()
    arr, w, h, meta = inspect_and_decode(raw)
    meta["source_bytes"] = len(raw)
    return arr, w, h, raw, meta


def encode_png(array: np.ndarray) -> bytes:
    from io import BytesIO

    if array.ndim == 2:
        img = Image.fromarray(array)
    else:
        rgb = array[:, :, ::-1] if array.shape[2] >= 3 else array
        img = Image.fromarray(np.ascontiguousarray(rgb[:, :, :3]))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def array_to_rgb(array: np.ndarray) -> np.ndarray:
    if array.ndim == 2:
        return np.stack([array, array, array], axis=-1)
    rgb = array[:, :, ::-1] if array.shape[2] >= 3 else array
    return np.ascontiguousarray(rgb[:, :, :3])


def qimage_to_bgr(qimage) -> np.ndarray:
    """Convert a QImage (e.g. Windows screenshot clipboard) to BGR uint8."""
    from PySide6.QtGui import QImage

    if qimage is None or qimage.isNull():
        raise ImageImportError("Clipboard does not contain an image.")
    img = qimage.convertToFormat(QImage.Format.Format_RGB888)
    w, h = img.width(), img.height()
    bpl = img.bytesPerLine()
    buf = bytes(img.bits())
    row = np.frombuffer(buf, dtype=np.uint8).reshape(h, bpl)
    rgb = row[:, : w * 3].reshape(h, w, 3).copy()
    return np.ascontiguousarray(rgb[:, :, ::-1])


IMAGE_DIALOG_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp);;All Files (*.*)"


def downscale_for_display(array: np.ndarray, max_edge: int = 4096) -> tuple[np.ndarray, float]:
    """Return (display_copy, scale_from_original). Calculations still use original coords."""
    h, w = array.shape[:2]
    edge = max(h, w)
    if edge <= max_edge:
        return array, 1.0
    scale = max_edge / edge
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    import cv2

    return cv2.resize(array, (new_w, new_h), interpolation=cv2.INTER_AREA), scale
