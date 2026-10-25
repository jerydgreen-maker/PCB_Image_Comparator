"""QDoubleSpinBox that accepts both 0.25 and 0,25 as 0.25."""

from __future__ import annotations

from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QDoubleSpinBox


def parse_decimal(text: str) -> float | None:
    raw = (text or "").strip().replace(" ", "").replace("\u00a0", "")
    if not raw:
        return None
    raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


class DualDecimalSpinBox(QDoubleSpinBox):
    """Locale-independent decimals: comma and dot both mean the radix."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setKeyboardTracking(False)

    def validate(self, text: str, pos: int):  # noqa: N802
        suffix = self.suffix()
        body = text[: -len(suffix)] if suffix and text.endswith(suffix) else text
        cleaned = body.strip().replace(" ", "")
        if cleaned in ("", "+", "-", ".", ","):
            return QValidator.State.Intermediate, text, pos
        trial = cleaned.replace(",", ".")
        try:
            float(trial)
        except ValueError:
            return QValidator.State.Invalid, text, pos
        return QValidator.State.Acceptable, text, pos

    def valueFromText(self, text: str) -> float:  # noqa: N802
        suffix = self.suffix()
        body = text[: -len(suffix)] if suffix and text.endswith(suffix) else text
        parsed = parse_decimal(body)
        if parsed is None:
            return float(self.value())
        lo, hi = self.minimum(), self.maximum()
        return max(lo, min(hi, parsed))

    def textFromValue(self, value: float) -> str:  # noqa: N802
        return f"{value:.{self.decimals()}f}"
