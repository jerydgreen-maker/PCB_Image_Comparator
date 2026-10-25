"""High-resolution PNG export of comparison views. Original files stay untouched."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from importers.image_import import array_to_rgb


def save_rgb(path: str | Path, rgb: np.ndarray, *, jpeg_quality: int | None = None) -> None:
    path = Path(path)
    img = Image.fromarray(np.ascontiguousarray(rgb.astype(np.uint8)))
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        q = 95 if jpeg_quality is None else int(jpeg_quality)
        img.save(path, format="JPEG", quality=q)
        return
    img.save(path, format="PNG")


def export_annotated(
    path: str | Path,
    rgb: np.ndarray,
    points: list[tuple[float, float, str, tuple[int, int, int]]],
    *,
    jpeg_quality: int | None = None,
) -> tuple[int, int]:
    """Render annotations onto the given (original-resolution) RGB array."""
    out = annotate_points(rgb, points)
    save_rgb(path, out, jpeg_quality=jpeg_quality)
    h, w = out.shape[:2]
    return w, h


def scale_rgb(rgb: np.ndarray, scale: float) -> np.ndarray:
    if abs(scale - 1.0) < 1e-9:
        return rgb
    import cv2

    h, w = rgb.shape[:2]
    return cv2.resize(rgb, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)


def overlay_images(a: np.ndarray, b_on_a: np.ndarray, opacity: float) -> np.ndarray:
    a_rgb = array_to_rgb(a).astype(np.float32)
    b_rgb = array_to_rgb(b_on_a).astype(np.float32)
    if a_rgb.shape != b_rgb.shape:
        import cv2

        b_rgb = cv2.resize(b_rgb, (a_rgb.shape[1], a_rgb.shape[0]))
    t = float(np.clip(opacity, 0.0, 1.0))
    out = a_rgb * (1.0 - t) + b_rgb * t
    return out.astype(np.uint8)


def difference_image(a: np.ndarray, b_on_a: np.ndarray) -> np.ndarray:
    a_rgb = array_to_rgb(a).astype(np.int16)
    b_rgb = array_to_rgb(b_on_a).astype(np.int16)
    if a_rgb.shape != b_rgb.shape:
        import cv2

        b_rgb = cv2.resize(b_rgb, (a_rgb.shape[1], a_rgb.shape[0]))
    diff = np.abs(a_rgb - b_rgb).astype(np.uint8)
    return diff


def edge_difference(a: np.ndarray, b_on_a: np.ndarray) -> np.ndarray:
    import cv2

    def edges(img):
        g = cv2.cvtColor(array_to_rgb(img), cv2.COLOR_RGB2GRAY)
        return cv2.Canny(g, 60, 180)

    ea = edges(a)
    eb = edges(b_on_a)
    if ea.shape != eb.shape:
        eb = cv2.resize(eb, (ea.shape[1], ea.shape[0]))
    # matched → dark, mismatch → bright
    xor = cv2.bitwise_xor(ea, eb)
    both = cv2.bitwise_and(ea, eb)
    rgb = np.zeros((ea.shape[0], ea.shape[1], 3), dtype=np.uint8)
    rgb[..., 1] = both  # subtle green where both have edges
    rgb[..., 0] = xor  # red mismatch
    rgb[..., 2] = xor
    return rgb


def annotate_points(rgb: np.ndarray, points: list[tuple[float, float, str, tuple[int, int, int]]]) -> np.ndarray:
    img = Image.fromarray(rgb.copy())
    draw = ImageDraw.Draw(img)
    for x, y, label, color in points:
        r = 5
        draw.ellipse((x - r, y - r, x + r, y + r), outline=color, width=2)
        draw.text((x + 7, y - 7), label, fill=color)
    return np.array(img)
