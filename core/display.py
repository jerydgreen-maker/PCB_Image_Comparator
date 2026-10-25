"""Display-only processing. Always starts from a copy of the current work image."""

from __future__ import annotations

import numpy as np

from models.adjustments import ImageAdjustments


def apply_adjustments(bgr: np.ndarray, adj: ImageAdjustments) -> np.ndarray:
    """Return a processed copy. `bgr` is never mutated."""
    if adj is None or adj.is_neutral():
        return bgr
    img = np.ascontiguousarray(bgr, dtype=np.float32)
    if img.ndim == 2:
        img = np.stack([img, img, img], axis=-1)
    if adj.exposure:
        img *= 2.0 ** (adj.exposure / 50.0)
    if adj.brightness:
        img += adj.brightness * 2.55
    if adj.contrast:
        f = (259.0 * (adj.contrast + 100.0)) / (259.0 * 100.0 - adj.contrast) if adj.contrast != 259 else 1.0
        img = f * (img - 128.0) + 128.0
    if abs(adj.gamma - 1.0) > 1e-6:
        g = max(0.20, min(5.0, adj.gamma))
        img = 255.0 * np.power(np.clip(img, 0, 255) / 255.0, 1.0 / g)
    if adj.shadows or adj.highlights:
        img = _shadows_highlights(img, adj.shadows, adj.highlights)
    img = np.clip(img, 0, 255)
    if adj.saturation or adj.grayscale:
        gray = img.mean(axis=2, keepdims=True)
        if adj.grayscale:
            img = np.repeat(gray, 3, axis=2)
        else:
            s = 1.0 + adj.saturation / 100.0
            img = gray + (img - gray) * s
    if adj.invert:
        img = 255.0 - img
    out = np.clip(img, 0, 255).astype(np.uint8)
    if adj.clahe:
        out = _clahe(out)
    if adj.clarity:
        out = _clarity(out, adj.clarity)
    if adj.denoise:
        out = _denoise(out, adj.denoise)
    if adj.sharpness:
        out = _sharpen(out, adj.sharpness)
    if adj.edge_enhance:
        out = _sharpen(out, 25.0)
    return out


def _shadows_highlights(img: np.ndarray, shadows: float, highlights: float) -> np.ndarray:
    lum = img.mean(axis=2, keepdims=True)
    t = np.clip(lum / 255.0, 0.0, 1.0)
    if shadows:
        lift = (1.0 - t) * (shadows / 100.0) * 48.0
        img = img + lift
    if highlights:
        crush = t * (highlights / 100.0) * 48.0
        img = img - crush
    return img


def _sharpen(bgr: np.ndarray, amount: float) -> np.ndarray:
    """amount -100..100. Negative softens. Modest kernel to avoid extreme halos."""
    import cv2

    t = float(np.clip(amount, -100.0, 100.0)) / 100.0
    blur = cv2.GaussianBlur(bgr, (0, 0), 1.2)
    if t >= 0:
        return cv2.addWeighted(bgr, 1.0 + 0.8 * t, blur, -0.8 * t, 0)
    return cv2.addWeighted(bgr, 1.0 + t, blur, -t, 0)


def _clarity(bgr: np.ndarray, amount: float) -> np.ndarray:
    """Local contrast / mid-frequency clarity."""
    import cv2

    t = float(np.clip(amount, 0.0, 100.0)) / 100.0
    if t <= 0:
        return bgr
    blur = cv2.GaussianBlur(bgr, (0, 0), 3.2)
    return cv2.addWeighted(bgr, 1.0 + 0.55 * t, blur, -0.55 * t, 0)


def _denoise(bgr: np.ndarray, amount: float) -> np.ndarray:
    import cv2

    t = float(np.clip(amount, 0.0, 100.0))
    if t <= 0:
        return bgr
    h = 2.0 + t * 0.10
    return cv2.fastNlMeansDenoisingColored(bgr, None, h, h, 5, 15)


def _clahe(bgr: np.ndarray) -> np.ndarray:
    import cv2

    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l2 = clahe.apply(l)
    return cv2.cvtColor(cv2.merge([l2, a, b]), cv2.COLOR_LAB2BGR)
