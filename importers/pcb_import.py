"""Dispatch practical PCB/CAD interchange formats. Do not fake unsupported natives."""

from __future__ import annotations

from pathlib import Path

from models.enums import Side
from models.vector import VectorDocument


class PcbImportError(ValueError):
    pass


SUPPORTED_HINTS = (
    "Gerber / Gerber ZIP",
    "KiCad PCB (.kicad_pcb)",
    "DXF",
    "SVG",
    "Excellon Drill",
    "Eagle BRD (XML)",
    "CSV coordinates",
    "EasyEDA intermediate JSON (optional, not required)",
)


UNSUPPORTED = {
    ".pcbdoc": "Native Altium binary PCB files are not parsed. Export Gerber, DXF, or ODB++.",
    ".pcb": "This extension is ambiguous and native formats are not guessed. Use Gerber ZIP, DXF, SVG, or KiCad.",
    ".schdoc": "Altium schematic files are not a PCB geometry source.",
}


def detect_kind(path: str | Path) -> str:
    p = Path(path)
    ext = p.suffix.lower()
    name = p.name.lower()
    if ext in UNSUPPORTED:
        return "unsupported"
    if ext == ".zip" or name.endswith(".gbr.zip"):
        return "gerber_zip"
    if ext in {".gbr", ".ger", ".gtl", ".gbl", ".gto", ".gbo", ".gts", ".gbs", ".gko", ".gm1"}:
        return "gerber"
    if ext == ".kicad_pcb":
        return "kicad"
    if ext == ".dxf":
        return "dxf"
    if ext == ".svg":
        return "svg"
    if ext in {".drl", ".xln"}:
        return "excellon"
    if ext == ".brd":
        return "eagle"
    if ext == ".csv":
        return "csv"
    if ext == ".json":
        return "easyeda_optional"
    return "unknown"


def load_pcb(path: str | Path, side: Side = Side.B) -> VectorDocument:
    path = Path(path)
    kind = detect_kind(path)
    if kind == "unsupported":
        raise PcbImportError(UNSUPPORTED.get(path.suffix.lower(), "Unsupported native format."))
    if kind == "unknown":
        raise PcbImportError(
            f"Unsupported file: {path.name}. "
            "Use Gerber / Gerber ZIP, KiCad .kicad_pcb, DXF, SVG, Excellon, Eagle XML .brd, or CSV."
        )
    if kind == "gerber_zip":
        from importers.gerber_import import load_gerber_zip

        return load_gerber_zip(path, side)
    if kind == "gerber":
        from importers.gerber_import import load_gerber

        return load_gerber(path, side)
    if kind == "kicad":
        from importers.kicad_import import load_kicad_pcb

        return load_kicad_pcb(path, side)
    if kind == "dxf":
        from importers.dxf_import import load_dxf

        return load_dxf(path, side)
    if kind == "svg":
        from importers.svg_import import load_svg

        return load_svg(path, side)
    if kind == "excellon":
        from importers.excellon_import import load_excellon

        return load_excellon(path, side)
    if kind == "eagle":
        from importers.eagle_import import load_eagle_brd

        return load_eagle_brd(path, side)
    if kind == "easyeda_optional":
        from importers.easyeda_import import load_easyeda

        return load_easyeda(path, side)
    raise PcbImportError(f"No importer for {path.name}.")
