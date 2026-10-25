"""Non-destructive AUTO ENHANCE. Proposes display parameters only."""

from __future__ import annotations

import numpy as np

from models.adjustments import ImageAdjustments


def propose_auto_adjustments(bgr: np.ndarray) -> ImageAdjustments:
    """Histogram-based proposal. Never writes source pixels."""
    adj = ImageAdjustments()
    adj.enhance_mode = "AUTO"
    if bgr is None or bgr.size == 0:
        return adj
    img = np.ascontiguousarray(bgr)
    if img.ndim == 2:
        gray = img.astype(np.float64)
    else:
        gray = img.mean(axis=2)
    sample = gray if gray.size < 250_000 else gray[::4, ::4]
    lo, hi = np.percentile(sample, (4.0, 96.0))
    span = max(8.0, float(hi - lo))
    # Stretch toward a usable mid-tone range without inventing edges.
    if lo > 20:
        adj.brightness = float(np.clip((lo - 16.0) * -0.35, -28, 0))
    elif hi < 220:
        adj.brightness = float(np.clip((220.0 - hi) * 0.25, 0, 22))
    if span < 140:
        adj.contrast = float(np.clip((160.0 - span) * 0.35, 8, 36))
    else:
        adj.contrast = 8.0
    adj.clarity = 18.0
    adj.clahe = True
    adj.gamma = 0.95 if float(np.median(sample)) < 90 else 1.0
    adj.sharpness = 6.0
    adj.denoise = 8.0 if float(sample.std()) > 38 else 0.0
    return adj
