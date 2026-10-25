"""RS-274X Gerber and Gerber ZIP. Practical fabrication-package import."""

from __future__ import annotations

import math
import re
import zipfile
from pathlib import Path

from models.enums import LayerName, Side
from models.vector import VectorDocument, VectorPrimitive


class GerberImportError(ValueError):
    pass


_LAYER_HINTS = (
    (("edge", "outline", "gko", "gm1", "profile"), LayerName.BOARD_OUTLINE),
    (("gto", "gbo", "silk"), LayerName.SILKSCREEN),
    (("gts", "gbs", "mask"), LayerName.OTHER),
    (("gtl", "gbl", "gtp", "gbp", "copper"), LayerName.COPPER),
    (("drl", "xln"), LayerName.HOLES),
)


def classify_gerber_name(name: str) -> LayerName:
    n = Path(name).name.lower()
    for keys, layer in _LAYER_HINTS:
        if any(k in n for k in keys):
            return layer
    if n.endswith((".drl", ".xln")):
        return LayerName.HOLES
    return LayerName.OTHER


def _parse_fs(text: str) -> tuple[int, int, bool]:
    m = re.search(r"%FS([LT])A?X(\d)(\d)Y(\d)(\d)\*%", text)
    if not m:
        return 4, 4, True
    return int(m.group(2)), int(m.group(3)), m.group(1) == "L"


def _parse_mo(text: str) -> float:
    if re.search(r"%MOIN\*%", text):
        return 25.4
    return 1.0


def _coord(token: str, int_d: int, dec: int, leading: bool) -> float:
    sign = -1.0 if token.startswith("-") else 1.0
    digits = token.lstrip("+-")
    if "." in digits:
        return sign * float(digits)
    if leading:
        return sign * float(int(digits or "0")) / (10**dec)
    padded = digits.ljust(int_d + dec, "0")
    return sign * float(padded) / (10**dec)


def load_gerber(path: str | Path, side: Side = Side.B, *, layer: LayerName | None = None) -> VectorDocument:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        raise GerberImportError(f"Cannot read Gerber: {exc}") from exc
    return parse_gerber_text(text, side=side, source=str(path), layer=layer or classify_gerber_name(path.name))


def parse_gerber_text(text: str, *, side: Side, source: str, layer: LayerName) -> VectorDocument:
    int_d, dec, leading = _parse_fs(text)
    to_mm = _parse_mo(text)
    primitives: list[VectorPrimitive] = []
    x = y = 0.0
    last = (0.0, 0.0)
    interpolation = "lin"
    region = False
    region_pts: list[tuple[float, float]] = []
    compact = text.replace("\n", "").replace("\r", "")
    for raw in re.findall(r"%[^%]*%|[^*]+[*]", compact):
        if raw.startswith("%"):
            continue
        if "G36" in raw:
            region = True
            region_pts = []
            continue
        if "G37" in raw:
            if len(region_pts) >= 2:
                primitives.append(VectorPrimitive(kind="polyline", layer=layer, points=list(region_pts), closed=True, unit="mm"))
            region = False
            continue
        if "G01" in raw or re.search(r"G1[^0-9]", raw):
            interpolation = "lin"
        if "G02" in raw or re.search(r"G2[^0-9]", raw):
            interpolation = "cw"
        if "G03" in raw or re.search(r"G3[^0-9]", raw):
            interpolation = "ccw"
        m = re.search(
            r"(?:X([+-]?\d*\.?\d+))?(?:Y([+-]?\d*\.?\d+))?(?:I([+-]?\d*\.?\d+))?(?:J([+-]?\d*\.?\d+))?(?:D0?([123]))?",
            raw,
        )
        if not m or not any(m.group(i) for i in range(1, 6)):
            continue
        nx, ny = x, y
        if m.group(1):
            nx = _coord(m.group(1), int_d, dec, leading) * to_mm
        if m.group(2):
            ny = _coord(m.group(2), int_d, dec, leading) * to_mm
        dcode = m.group(5)
        if dcode == "2":
            last = (nx, ny)
            x, y = nx, ny
            if region:
                region_pts.append((nx, ny))
            continue
        if dcode == "3":
            primitives.append(VectorPrimitive(kind="pad", layer=LayerName.PADS, points=[(nx, ny)], unit="mm"))
            last = (nx, ny)
            x, y = nx, ny
            continue
        if dcode == "1":
            if region:
                region_pts.append((nx, ny))
            elif interpolation == "lin":
                primitives.append(VectorPrimitive(kind="line", layer=layer, points=[last, (nx, ny)], unit="mm"))
            else:
                i = _coord(m.group(3), int_d, dec, leading) * to_mm if m.group(3) else 0.0
                j = _coord(m.group(4), int_d, dec, leading) * to_mm if m.group(4) else 0.0
                primitives.append(
                    VectorPrimitive(
                        kind="arc",
                        layer=layer,
                        points=[last, (nx, ny)],
                        radius=math.hypot(i, j),
                        attributes={"cx": last[0] + i, "cy": last[1] + j, "dir": interpolation},
                        unit="mm",
                    )
                )
            last = (nx, ny)
            x, y = nx, ny
    notes = f"Gerber {Path(source).name}: {len(primitives)} primitives, layer={layer.value}"
    return VectorDocument(source=source, side=side, primitives=primitives, notes=notes, unit="mm")


def load_gerber_zip(path: str | Path, side: Side = Side.B) -> VectorDocument:
    path = Path(path)
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise GerberImportError(f"Not a ZIP archive: {exc}") from exc
    all_prims: list[VectorPrimitive] = []
    identified: list[str] = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = info.filename
        low = name.lower()
        if low.endswith((".png", ".jpg", ".jpeg", ".pdf", ".html")):
            continue
        data = zf.read(info)
        try:
            text = data.decode("utf-8", errors="ignore")
        except Exception:
            continue
        layer = classify_gerber_name(name)
        if "%FS" in text or "%MO" in text or "G04" in text:
            doc = parse_gerber_text(text, side=side, source=name, layer=layer)
            all_prims.extend(doc.primitives)
            identified.append(f"{Path(name).name}→{layer.value}")
        elif _looks_excellon(text):
            from importers.excellon_import import parse_excellon_text

            try:
                doc = parse_excellon_text(text, side=side, source=name)
            except Exception:
                continue
            all_prims.extend(doc.primitives)
            identified.append(f"{Path(name).name}→HOLES")
    if not all_prims:
        raise GerberImportError("ZIP contained no recognizable Gerber or Excellon files.")
    notes = "Gerber ZIP layers: " + ", ".join(identified)
    return VectorDocument(source=str(path), side=side, primitives=all_prims, notes=notes, unit="mm")


def _looks_excellon(text: str) -> bool:
    head = text[:800].upper()
    return "M48" in head or "T01C" in head or bool(re.search(r"T\d+C", head))
