"""KiCad .kicad_pcb s-expression importer. Parses Edge.Cuts, pads, vias, footprints."""

from __future__ import annotations

from pathlib import Path

from models.enums import LayerName, Side
from models.vector import VectorDocument, VectorPrimitive


class KicadImportError(ValueError):
    pass


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c == "(":
            tokens.append("(")
            i += 1
            continue
        if c == ")":
            tokens.append(")")
            i += 1
            continue
        if c == '"':
            j = i + 1
            buf = []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                    continue
                buf.append(text[j])
                j += 1
            tokens.append("".join(buf))
            i = j + 1
            continue
        j = i
        while j < n and text[j] not in " \t\r\n()":
            j += 1
        tokens.append(text[i:j])
        i = j
    return tokens


def _parse(tokens: list[str]):
    def read(idx: int):
        if idx >= len(tokens):
            raise KicadImportError("Unexpected end of .kicad_pcb")
        t = tokens[idx]
        if t == "(":
            items = []
            idx += 1
            while idx < len(tokens) and tokens[idx] != ")":
                node, idx = read(idx)
                items.append(node)
            if idx >= len(tokens):
                raise KicadImportError("Unbalanced parentheses in .kicad_pcb")
            return items, idx + 1
        if t == ")":
            raise KicadImportError("Unexpected ')'")
        return t, idx + 1

    tree, idx = read(0)
    return tree


def _head(node):
    return node[0] if isinstance(node, list) and node else None


def _find(node, name: str) -> list:
    out = []
    if not isinstance(node, list):
        return out
    if _head(node) == name:
        out.append(node)
    for ch in node[1:]:
        out.extend(_find(ch, name))
    return out


def _xy(node) -> tuple[float, float] | None:
    if not isinstance(node, list) or len(node) < 3:
        return None
    try:
        return float(node[1]), float(node[2])
    except (TypeError, ValueError):
        return None


def _layer_of(node) -> str:
    for ch in node if isinstance(node, list) else []:
        if isinstance(ch, list) and _head(ch) == "layer" and len(ch) > 1:
            return str(ch[1])
        if isinstance(ch, list) and _head(ch) == "layers" and len(ch) > 1:
            return str(ch[1])
    return ""


def _map_layer(name: str) -> LayerName:
    n = (name or "").lower()
    if "edge.cuts" in n or "edge" in n:
        return LayerName.BOARD_OUTLINE
    if "pad" in n:
        return LayerName.PADS
    if "via" in n:
        return LayerName.VIAS
    if "f.silk" in n or "b.silk" in n or "silk" in n:
        return LayerName.SILKSCREEN
    if "f.cu" in n or "b.cu" in n or ".cu" in n:
        return LayerName.COPPER
    return LayerName.OTHER


def load_kicad_pcb(path: str | Path, side: Side = Side.B) -> VectorDocument:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        raise KicadImportError(f"Cannot read KiCad PCB: {exc}") from exc
    if "(kicad_pcb" not in text[:400] and "kicad_pcb" not in text[:400]:
        raise KicadImportError("File is not a KiCad PCB (missing kicad_pcb header).")
    try:
        tree = _parse(_tokenize(text))
    except KicadImportError:
        raise
    except Exception as exc:
        raise KicadImportError(f"Could not parse KiCad s-expression: {exc}") from exc
    primitives: list[VectorPrimitive] = []

    for tag, kind in (("gr_line", "line"), ("fp_line", "line"), ("gr_poly", "polyline"), ("fp_poly", "polyline")):
        for node in _find(tree, tag):
            pts = []
            start = end = None
            for ch in node[1:]:
                if isinstance(ch, list) and _head(ch) == "start":
                    start = _xy(ch)
                elif isinstance(ch, list) and _head(ch) == "end":
                    end = _xy(ch)
                elif isinstance(ch, list) and _head(ch) == "pts":
                    for xy in ch[1:]:
                        if isinstance(xy, list) and _head(xy) == "xy":
                            p = _xy(xy)
                            if p:
                                pts.append(p)
            layer = _map_layer(_layer_of(node))
            if start and end:
                primitives.append(VectorPrimitive(kind="line", layer=layer, points=[start, end], unit="mm"))
            elif pts:
                primitives.append(VectorPrimitive(kind="polyline", layer=layer, points=pts, closed=True, unit="mm"))

    for tag in ("gr_circle", "fp_circle"):
        for node in _find(tree, tag):
            center = end = None
            for ch in node[1:]:
                if isinstance(ch, list) and _head(ch) == "center":
                    center = _xy(ch)
                elif isinstance(ch, list) and _head(ch) == "end":
                    end = _xy(ch)
            if center:
                r = None
                if end:
                    r = ((end[0] - center[0]) ** 2 + (end[1] - center[1]) ** 2) ** 0.5
                primitives.append(
                    VectorPrimitive(kind="circle", layer=_map_layer(_layer_of(node)), points=[center], radius=r, unit="mm")
                )

    for tag in ("gr_arc", "fp_arc"):
        for node in _find(tree, tag):
            start = mid = end = None
            for ch in node[1:]:
                if isinstance(ch, list) and _head(ch) == "start":
                    start = _xy(ch)
                elif isinstance(ch, list) and _head(ch) == "mid":
                    mid = _xy(ch)
                elif isinstance(ch, list) and _head(ch) == "end":
                    end = _xy(ch)
            pts = [p for p in (start, mid, end) if p]
            if len(pts) >= 2:
                primitives.append(VectorPrimitive(kind="arc", layer=_map_layer(_layer_of(node)), points=pts, unit="mm"))

    for node in _find(tree, "via"):
        at = None
        size = drill = None
        for ch in node[1:]:
            if isinstance(ch, list) and _head(ch) == "at":
                at = _xy(ch)
            elif isinstance(ch, list) and _head(ch) == "size":
                try:
                    size = float(ch[1])
                except (TypeError, ValueError, IndexError):
                    pass
            elif isinstance(ch, list) and _head(ch) == "drill":
                try:
                    drill = float(ch[1])
                except (TypeError, ValueError, IndexError):
                    pass
        if at:
            primitives.append(
                VectorPrimitive(kind="via", layer=LayerName.VIAS, points=[at], radius=(size or 0) / 2.0 or None, attributes={"drill": drill}, unit="mm")
            )
            if drill:
                primitives.append(VectorPrimitive(kind="hole", layer=LayerName.HOLES, points=[at], radius=drill / 2.0, unit="mm"))

    for node in _find(tree, "pad"):
        at = None
        size = None
        drill = None
        ptype = node[2] if len(node) > 2 else ""
        for ch in node[1:]:
            if isinstance(ch, list) and _head(ch) == "at":
                at = _xy(ch)
            elif isinstance(ch, list) and _head(ch) == "size":
                try:
                    size = (float(ch[1]), float(ch[2]) if len(ch) > 2 else float(ch[1]))
                except (TypeError, ValueError, IndexError):
                    pass
            elif isinstance(ch, list) and _head(ch) == "drill":
                try:
                    drill = float(ch[1])
                except (TypeError, ValueError, IndexError):
                    pass
        if at:
            primitives.append(
                VectorPrimitive(
                    kind="pad",
                    layer=LayerName.PADS,
                    points=[at],
                    attributes={"size": size, "type": ptype},
                    unit="mm",
                )
            )
            if drill:
                primitives.append(VectorPrimitive(kind="hole", layer=LayerName.HOLES, points=[at], radius=drill / 2.0, unit="mm"))

    if not primitives:
        raise KicadImportError("KiCad file parsed, but no Edge.Cuts / pads / vias / copper geometry was found.")
    notes = f"KiCad PCB: {len(primitives)} primitives from {path.name}"
    return VectorDocument(source=str(path), side=side, primitives=primitives, notes=notes, unit="mm")
