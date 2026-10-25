"""Optional Super Resolution — architecture only.

P2. A local model/library is required before this can produce pixels.
Until then the feature is BLOCKED / TODO. Never pretend it exists.
Derived visual data is never trustworthy metrology.
"""

from __future__ import annotations

AVAILABLE = False
STATUS = "BLOCKED"
REASON = "No local super-resolution model or library is installed."


def super_resolution_status() -> dict:
    return {
        "available": AVAILABLE,
        "status": STATUS,
        "reason": REASON,
        "factors": (2, 4),
        "metrology": "never",
        "label": "ENHANCED / SUPER-RESOLUTION VIEW",
    }


def upscale(_array, factor: int = 2):
    raise NotImplementedError(
        f"Super Resolution is {STATUS}: {REASON} (requested {factor}x)."
    )
