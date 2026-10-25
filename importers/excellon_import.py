"""Excellon drill import."""

from __future__ import annotations

import re
from pathlib import Path

from models.enums import LayerName, Side
from models.vector import VectorDocument, VectorPrimitive


class ExcellonImportError(ValueError):
    pass


def load_excellon(path: str | Path, side: Side = Side.B) -> VectorDocument:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        raise ExcellonImportError(f"Cannot read Excellon: {exc}") from exc
    return parse_excellon_text(text, side=side, source=str(path))


def parse_excellon_text(text: str, *, side: Side, source: str) -> VectorDocument:
    to_mm = 25.4 if re.search(r"INCH", text, re.I) else 1.0
    tools: dict[str, float] = {}
    for m in re.finditer(r"T(\d+)\s*C\s*([0-9.]+)", text, re.I):
        tools[m.group(1).lstrip("0") or "0"] = float(m.group(2)) * to_mm
    primitives: list[VectorPrimitive] = []
    current = None
    dec = 4
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(";"):
            continue
        tm = re.match(r"T(\d+)$", s, re.I)
        if tm:
            current = tm.group(1).lstrip("0") or "0"
            continue
        m = re.search(r"X([+-]?\d*\.?\d+)\s*Y([+-]?\d*\.?\d+)", s, re.I)
        if not m:
            continue
        x = _num(m.group(1), dec) * to_mm
        y = _num(m.group(2), dec) * to_mm
        dia = tools.get(current or "", None)
        primitives.append(
            VectorPrimitive(
                kind="hole",
                layer=LayerName.HOLES,
                points=[(x, y)],
                radius=(dia / 2.0) if dia else None,
                unit="mm",
            )
        )
    if not primitives:
        raise ExcellonImportError("No drill hits found.")
    return VectorDocument(source=source, side=side, primitives=primitives, notes=f"Excellon: {len(primitives)} hits", unit="mm")


def _num(token: str, dec: int) -> float:
    if "." in token:
        return float(token)
    sign = -1.0 if token.startswith("-") else 1.0
    digits = token.lstrip("+-")
    return sign * float(int(digits or "0")) / (10**dec)
