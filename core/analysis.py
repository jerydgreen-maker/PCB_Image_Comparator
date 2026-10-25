"""Read-only geometry comparison. Never changes registration or points.

POINT MATCH RATE
    matched confirmed CONTROL points / total confirmed CONTROL points × 100

GEOMETRIC ACCURACY SCORE
    For each confirmed point:
        normalized_error = ERROR / tolerance
        point_score = max(0, 1 - normalized_error)
    overall = mean(point_score) × 100

These percentages are metric results vs the user tolerance.
They do NOT mean the PCB is physically N% identical.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

from core.diagnostics import common_translation, mirror_improvement, rotation_mismatch, scale_mismatch
from core.residuals import PointResidual
from core.statistics import summarize
from models.enums import Confidence, PointType, ResultState, Side, TransformMode, TransformStatus, Unit
from models.units import px_to_unit


@dataclass
class PointAnalysis:
    id: str
    group: str
    source_side: str
    source_x: float
    source_y: float
    expected_x: float
    expected_y: float
    actual_x: float
    actual_y: float
    dx: float
    dy: float
    error: float
    tolerance: float | None
    status: str
    included: bool
    exclude_reason: str = ""
    unit: str = "px"
    tolerance_applied: str = ""

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class ComparisonResult:
    generated_at: str
    fingerprint: str
    stale: bool = False
    unit: str = "px"
    tolerance: float = 3.0
    tolerance_label: str = "3 px"
    confirmed: int = 0
    matched: int = 0
    mismatched: int = 0
    expected_only: int = 0
    excluded: int = 0
    point_match_rate: float | None = None
    geometric_score: float | None = None
    mean_error: float | None = None
    median_error: float | None = None
    rmse: float | None = None
    max_error: float | None = None
    mean_dx: float | None = None
    mean_dy: float | None = None
    points: list[PointAnalysis] = field(default_factory=list)
    groups: list[dict] = field(default_factory=list)
    mesh_edges: list[dict] = field(default_factory=list)
    mesh_triangles: list[dict] = field(default_factory=list)
    dimensions: list[dict] = field(default_factory=list)
    contours: list[dict] = field(default_factory=list)
    diagnostics: list[dict] = field(default_factory=list)
    header: dict = field(default_factory=dict)
    formulas: dict = field(default_factory=dict)
    log_text: str = ""

    def as_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "fingerprint": self.fingerprint,
            "stale": self.stale,
            "unit": self.unit,
            "tolerance": self.tolerance,
            "tolerance_label": self.tolerance_label,
            "confirmed": self.confirmed,
            "matched": self.matched,
            "mismatched": self.mismatched,
            "expected_only": self.expected_only,
            "excluded": self.excluded,
            "point_match_rate": self.point_match_rate,
            "geometric_score": self.geometric_score,
            "mean_error": self.mean_error,
            "median_error": self.median_error,
            "rmse": self.rmse,
            "max_error": self.max_error,
            "mean_dx": self.mean_dx,
            "mean_dy": self.mean_dy,
            "points": [p.as_dict() for p in self.points],
            "groups": list(self.groups),
            "mesh_edges": list(self.mesh_edges),
            "mesh_triangles": list(self.mesh_triangles),
            "dimensions": list(self.dimensions),
            "contours": list(self.contours),
            "diagnostics": list(self.diagnostics),
            "header": dict(self.header),
            "formulas": dict(self.formulas),
            "log_text": self.log_text,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "ComparisonResult | None":
        if not data:
            return None
        fields = set(PointAnalysis.__dataclass_fields__)
        pts = [PointAnalysis(**{k: v for k, v in p.items() if k in fields}) for p in data.get("points") or []]
        return cls(
            generated_at=data.get("generated_at", ""),
            fingerprint=data.get("fingerprint", ""),
            stale=bool(data.get("stale", False)),
            unit=data.get("unit", "px"),
            tolerance=float(data.get("tolerance", 3.0)),
            tolerance_label=data.get("tolerance_label", ""),
            confirmed=int(data.get("confirmed", 0)),
            matched=int(data.get("matched", 0)),
            mismatched=int(data.get("mismatched", 0)),
            expected_only=int(data.get("expected_only", 0)),
            excluded=int(data.get("excluded", 0)),
            point_match_rate=data.get("point_match_rate"),
            geometric_score=data.get("geometric_score"),
            mean_error=data.get("mean_error"),
            median_error=data.get("median_error"),
            rmse=data.get("rmse"),
            max_error=data.get("max_error"),
            mean_dx=data.get("mean_dx"),
            mean_dy=data.get("mean_dy"),
            points=pts,
            groups=list(data.get("groups") or []),
            mesh_edges=list(data.get("mesh_edges") or []),
            mesh_triangles=list(data.get("mesh_triangles") or []),
            dimensions=list(data.get("dimensions") or []),
            contours=list(data.get("contours") or []),
            diagnostics=list(data.get("diagnostics") or []),
            header=dict(data.get("header") or {}),
            formulas=dict(data.get("formulas") or {}),
            log_text=data.get("log_text", ""),
        )


def effective_tolerance_px(controller, group: str | None = None) -> tuple[float | None, str, Unit]:
    """Tolerance in work pixels. Physical units without calibration return None — never guessed."""
    proj = controller.data
    val = float(proj.tolerances.value_for_group(group))
    unit = proj.tolerances.unit
    inherited = not (group and group in proj.tolerances.group_match)
    src = "inherited GLOBAL" if inherited else f"group {group}"
    img = controller.image(Side.B) or controller.image(Side.A)
    if unit == Unit.PX:
        return val, f"{val:g} px ({src})", Unit.PX
    if img is None or not img.calibration.is_calibrated:
        return None, "NOT AVAILABLE — MM SCALE NOT SET", unit
    from models.units import physical_to_mm

    mm = physical_to_mm(val, unit)
    px = mm / img.calibration.mm_per_px
    return px, f"{val:g} {unit.value} ({src})", unit


def control_match_state(controller, lm) -> str:
    if lm.point_type not in (PointType.CONTROL, PointType.MEASUREMENT):
        return ""
    if not lm.enabled:
        return "UNVERIFIED"
    if not (lm.has_a and lm.has_b):
        return "EXPECTED_ONLY"
    if lm.confidence not in (Confidence.CONFIRMED, Confidence.CONFIRMED_VISIBLE):
        return "UNVERIFIED"
    xf = controller.accepted_transform()
    if xf is None:
        return "UNVERIFIED"
    ax, ay = controller._work_xy(Side.A, lm.a_x, lm.a_y)
    bx, by = controller._work_xy(Side.B, lm.b_x, lm.b_y)
    px, py = xf.apply_one(ax, ay)
    err = float(np.hypot(bx - px, by - py))
    tol, _, _ = effective_tolerance_px(controller, lm.group)
    if tol is None:
        return ""
    return "MATCH" if err <= tol else "MISMATCH"


def run_comparison(controller) -> ComparisonResult:
    """Pure analysis. Does not mutate landmarks, transform, mirror, or mesh topology."""
    proj = controller.data
    xf = controller.accepted_transform()
    tol_px, tol_label, unit = effective_tolerance_px(controller)
    physical = bool(getattr(controller, "physical_calibration_ready", lambda: False)())
    cal_b = proj.image_b.calibration.mm_per_px if proj.image_b and proj.image_b.calibration.is_calibrated else None
    if cal_b is not None and (cal_b <= 0 or not _finite(cal_b)):
        cal_b = None
        physical = False
    if not physical:
        display_unit = Unit.PX
    else:
        display_unit = unit if unit != Unit.PX else (proj.display_unit if cal_b else Unit.PX)

    def conv(px: float | None) -> float | None:
        if px is None:
            return None
        if not physical or display_unit == Unit.PX:
            return float(px)
        out = px_to_unit(px, cal_b, display_unit)
        return out

    rec = proj.current_transform
    header = {
        "project": proj.name,
        "path": str(controller.path) if controller.path else "",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_a": (proj.image_a.source_path or proj.image_a.notes) if proj.image_a else "none",
        "model_b": (proj.image_b.source_path or proj.image_b.notes) if proj.image_b else "none",
        "cal_a": (
            f"{proj.image_a.calibration.mm_per_px:.6f} mm/px"
            if proj.image_a and proj.image_a.calibration.is_calibrated
            else "not calibrated"
        ),
        "cal_b": (
            f"{proj.image_b.calibration.mm_per_px:.6f} mm/px"
            if proj.image_b and proj.image_b.calibration.is_calibrated
            else "not calibrated"
        ),
        "orientation_a": proj.image_a.orientation.status_label() if proj.image_a else "—",
        "orientation_b": proj.image_b.orientation.status_label() if proj.image_b else "—",
        "display_a": _display_label(proj.image_a),
        "display_b": _display_label(proj.image_b),
        "alignment": rec.status.value if rec else "NONE",
        "scale": rec.transform.scale if rec else None,
        "registration_scale": rec.transform.scale if rec else None,
        "rotation_deg": rec.transform.rotation_deg if rec else None,
        "tx": rec.transform.tx if rec else None,
        "ty": rec.transform.ty if rec else None,
        "tolerance": tol_label,
        "tolerance_global": f"{proj.tolerances.match:g} {proj.tolerances.unit.value}",
        "tolerance_groups": {
            g: f"{v:g} {proj.tolerances.unit.value}" for g, v in proj.tolerances.group_match.items()
        },
        "unit": display_unit.value,
        "physical_available": physical,
        "physical_note": (
            None
            if physical
            else "Physical mm scale has not been set. Result calculated in image coordinates."
        ),
        "display_processing_a": _adjust_label(proj.image_a),
        "display_processing_b": _adjust_label(proj.image_b),
        "image_a_info": _image_info(proj.image_a),
        "image_b_info": _image_info(proj.image_b),
    }
    zero = controller.landmark_of_type(PointType.ZERO)
    scale = controller.landmark_of_type(PointType.SCALE)
    header["zero"] = _lm_brief(zero)
    header["scale_point"] = _lm_brief(scale)
    header["registration_reference"] = header["scale_point"]
    header["physical_a"] = _physical_label(proj.image_a)
    header["physical_b"] = _physical_label(proj.image_b)

    points: list[PointAnalysis] = []
    residuals: list[PointResidual] = []
    expected_only = excluded = 0
    scores = []

    for lm in proj.landmarks:
        if lm.point_type not in (PointType.CONTROL, PointType.MEASUREMENT):
            continue
        origin = lm.extra.get("origin_side", "A")
        if not lm.enabled:
            excluded += 1
            points.append(_excluded(lm, "disabled", origin, display_unit.value, tol_px, conv))
            continue
        if not (lm.has_a or lm.has_b):
            excluded += 1
            continue
        if not (lm.has_a and lm.has_b):
            expected_only += 1
            src_side = "A" if lm.has_a else "B"
            sx, sy = (lm.a_x, lm.a_y) if lm.has_a else (lm.b_x, lm.b_y)
            if xf and lm.has_a:
                ex, ey = controller.predict_b_work(lm.a_x, lm.a_y) or (None, None)
            elif xf and lm.has_b:
                ex, ey = controller.predict_a_work(lm.b_x, lm.b_y) or (None, None)
            else:
                ex = ey = None
            points.append(
                PointAnalysis(
                    id=lm.id,
                    group=lm.group,
                    source_side=src_side,
                    source_x=conv(sx) if sx is not None else 0,
                    source_y=conv(sy) if sy is not None else 0,
                    expected_x=conv(ex) if ex is not None else 0,
                    expected_y=conv(ey) if ey is not None else 0,
                    actual_x=0,
                    actual_y=0,
                    dx=0,
                    dy=0,
                    error=0,
                    tolerance=conv(tol_px) if tol_px is not None else None,
                    status="EXPECTED_ONLY",
                    included=False,
                    exclude_reason="predicted only — no confirmed actual",
                    unit=display_unit.value,
                )
            )
            continue
        if lm.confidence not in (Confidence.CONFIRMED, Confidence.CONFIRMED_VISIBLE):
            excluded += 1
            points.append(_excluded(lm, f"confidence {lm.confidence.value}", origin, display_unit.value, tol_px, conv))
            continue
        if xf is None:
            excluded += 1
            points.append(_excluded(lm, "no accepted transform", origin, display_unit.value, tol_px, conv))
            continue
        ax, ay = controller._work_xy(Side.A, lm.a_x, lm.a_y)
        bx, by = controller._work_xy(Side.B, lm.b_x, lm.b_y)
        ex, ey = xf.apply_one(ax, ay)
        dx, dy = bx - ex, by - ey
        err = float(np.hypot(dx, dy))
        ptol, ptol_label, _ = effective_tolerance_px(controller, lm.group)
        if ptol is None:
            ptol, ptol_label = None, tol_label
            status = "MEASURED"
        else:
            status = "MATCH" if err <= ptol else "MISMATCH"
            scores.append(max(0.0, 1.0 - (err / ptol if ptol > 0 else 0.0)))
        residuals.append(
            PointResidual(lm.id, ex, ey, bx, by, dx, dy, err)
        )
        points.append(
            PointAnalysis(
                id=lm.id,
                group=lm.group or "UNGROUPED",
                source_side=origin,
                source_x=conv(ax) or 0,
                source_y=conv(ay) or 0,
                expected_x=conv(ex) or 0,
                expected_y=conv(ey) or 0,
                actual_x=conv(bx) or 0,
                actual_y=conv(by) or 0,
                dx=conv(dx) or 0,
                dy=conv(dy) or 0,
                error=conv(err) or 0,
                tolerance=conv(ptol) if ptol is not None else None,
                status=status,
                included=True,
                unit=display_unit.value,
                tolerance_applied=ptol_label,
            )
        )

    included = [p for p in points if p.included]
    matched = sum(1 for p in included if p.status == "MATCH")
    mismatched = sum(1 for p in included if p.status == "MISMATCH")
    confirmed = len(included)
    rate = (100.0 * matched / confirmed) if confirmed and tol_px is not None else None
    geo = (100.0 * float(np.mean(scores))) if scores and tol_px is not None else None
    errs = [p.error for p in included]
    stats = summarize(errs)
    mean_dx = float(np.mean([p.dx for p in included])) if included else None
    mean_dy = float(np.mean([p.dy for p in included])) if included else None

    groups = _group_rows(included)
    edges = controller.mesh_edge_rows()
    tris = controller.mesh_triangle_rows()
    dimensions = controller.dimension_rows() if hasattr(controller, "dimension_rows") else []
    contours = controller.contour_rows() if hasattr(controller, "contour_rows") else []

    findings = []
    if residuals and xf:
        grouped: dict[str, list[PointResidual]] = {}
        for r in residuals:
            lm = next((x for x in proj.landmarks if x.id == r.point_id), None)
            grouped.setdefault(lm.group if lm and lm.group else "UNGROUPED", []).append(r)
        f = common_translation(grouped, agree_tol=max(tol_px or 1.0, 1e-6))
        if f:
            findings.append(f.as_dict())
        origins = np.array([[r.predicted_x, r.predicted_y] for r in residuals], dtype=np.float64)
        f = scale_mismatch(residuals, origins)
        if f:
            findings.append(f.as_dict())
        f = rotation_mismatch(residuals, origins)
        if f:
            findings.append(f.as_dict())
        refs = controller.enabled_references()
        if refs and proj.image_b:
            src, dst, _ = controller.correspondence_arrays(refs)
            f = mirror_improvement(
                src,
                dst,
                proj.image_b.width,
                proj.image_b.height,
                rec.mode if rec else TransformMode.SIMILARITY,
            )
            if f:
                findings.append(f.as_dict())

    result = ComparisonResult(
        generated_at=header["generated_at"],
        fingerprint=controller.analysis_fingerprint(),
        unit=display_unit.value,
        tolerance=(conv(tol_px) if tol_px is not None else 0.0) or 0.0,
        tolerance_label=tol_label,
        confirmed=confirmed,
        matched=matched,
        mismatched=mismatched,
        expected_only=expected_only,
        excluded=excluded,
        point_match_rate=rate,
        geometric_score=geo,
        mean_error=stats.mean,
        median_error=stats.median,
        rmse=stats.rmse,
        max_error=stats.maximum,
        mean_dx=mean_dx,
        mean_dy=mean_dy,
        points=points,
        groups=groups,
        mesh_edges=edges,
        mesh_triangles=tris,
        dimensions=dimensions,
        contours=contours,
        diagnostics=findings,
        header=header,
        formulas={
            "POINT_MATCH_RATE": "matched confirmed CONTROL / total confirmed CONTROL × 100",
            "GEOMETRIC_ACCURACY_SCORE": "mean(max(0, 1 - ERROR/tolerance)) × 100",
            "disclaimer": "These percentages are vs the user tolerance. They do not claim physical PCB identity.",
        },
    )
    result.log_text = format_log(result)
    _ = TransformStatus
    return result


def _adjust_label(img) -> dict | str:
    if img is None:
        return "—"
    adj = getattr(img, "adjustments", None)
    if adj is None:
        return "neutral"
    return adj.as_dict() if hasattr(adj, "as_dict") else "neutral"


def _image_info(img) -> dict | str:
    if img is None:
        return "—"
    return {
        "width": img.width,
        "height": img.height,
        "format": getattr(img, "original_format", "") or "unknown",
        "mode": getattr(img, "original_mode", "") or "",
        "source_bytes": getattr(img, "source_byte_count", 0) or 0,
        "has_original_bytes": bool(getattr(img, "original_bytes", None)),
    }


def _display_label(img) -> str:
    if img is None:
        return "—"
    bits = []
    if img.view_flip_x:
        bits.append("DISPLAY MIRROR X")
    if img.view_flip_y:
        bits.append("DISPLAY MIRROR Y")
    return ", ".join(bits) if bits else "display normal"


def _finite(v) -> bool:
    try:
        return bool(np.isfinite(float(v)))
    except (TypeError, ValueError):
        return False


def _physical_label(img) -> str:
    if img is None:
        return "NOT CALIBRATED"
    cal = img.calibration
    if not cal.is_calibrated:
        return "NOT CALIBRATED"
    kind = "DERIVED" if cal.derived else "MEASURED"
    return f"{cal.mm_per_px:.6f} mm/px  {kind}"


def _lm_brief(lm) -> str:
    if lm is None:
        return "not set"
    return f"{lm.id} A=({lm.a_x},{lm.a_y}) B=({lm.b_x},{lm.b_y})"


def _excluded(lm, reason, origin, unit, tol_px, conv) -> PointAnalysis:
    return PointAnalysis(
        id=lm.id,
        group=lm.group or "UNGROUPED",
        source_side=origin,
        source_x=0,
        source_y=0,
        expected_x=0,
        expected_y=0,
        actual_x=0,
        actual_y=0,
        dx=0,
        dy=0,
        error=0,
        tolerance=conv(tol_px) if tol_px is not None else None,
        status="EXCLUDED",
        included=False,
        exclude_reason=reason,
        unit=unit,
    )


def _group_rows(included: list[PointAnalysis]) -> list[dict]:
    by: dict[str, list[PointAnalysis]] = {}
    for p in included:
        by.setdefault(p.group or "UNGROUPED", []).append(p)
    rows = []
    for name, pts in sorted(by.items()):
        m = sum(1 for p in pts if p.status == "MATCH")
        errs = [p.error for p in pts]
        st = summarize(errs)
        rows.append(
            {
                "group": name,
                "matched": m,
                "total": len(pts),
                "point_match_rate": 100.0 * m / len(pts) if pts else None,
                "rmse": st.rmse,
                "max": st.maximum,
                "mean": st.mean,
            }
        )
    return rows


def format_log(result: ComparisonResult) -> str:
    h = result.header
    lines = [
        "PCB IMAGE COMPARATOR",
        "GEOMETRY ANALYSIS LOG",
        "",
        f"Project: {h.get('project', '')}",
        f"Date/time: {h.get('generated_at', '')}",
        "",
        f"MODEL A: {h.get('model_a')}",
        f"MODEL B: {h.get('model_b')}",
        "",
        "REGISTRATION",
        f"Registration scale: {h.get('registration_scale', h.get('scale'))}",
        f"Rotation: {h.get('rotation_deg')}°",
        f"Translation: TX={h.get('tx')}  TY={h.get('ty')}",
        f"Alignment: {h.get('alignment')}",
        f"ZERO: {h.get('zero')}",
        f"Registration reference: {h.get('registration_reference', h.get('scale_point'))}",
        "",
        "PHYSICAL CALIBRATION",
        f"A: {h.get('physical_a', h.get('cal_a'))}",
        f"B: {h.get('physical_b', h.get('cal_b'))}",
        "",
        f"Orientation A (registration): {h.get('orientation_a')}",
        f"Orientation B (registration): {h.get('orientation_b')}",
        f"Display A: {h.get('display_a')}",
        f"Display B: {h.get('display_b')}",
        "",
        "DISPLAY PROCESSING ONLY (does not affect geometry)",
        f"  MODEL A DISPLAY: {_fmt_adj(h.get('display_processing_a'))}",
        f"  MODEL B DISPLAY: {_fmt_adj(h.get('display_processing_b'))}",
        "",
        f"IMAGE A SOURCE: {_fmt_info(h.get('image_a_info'))}",
        f"IMAGE B SOURCE: {_fmt_info(h.get('image_b_info'))}",
        "",
        f"Tolerance used: {result.tolerance_label}",
        f"GLOBAL TOLERANCE: {h.get('tolerance_global', result.tolerance_label)}",
        *_fmt_group_tols(h.get("tolerance_groups") or {}),
        f"Units: {result.unit}",
        "",
        "FORMULAS",
        f"  POINT MATCH RATE = {result.formulas.get('POINT_MATCH_RATE')}",
        f"  GEOMETRIC ACCURACY SCORE = {result.formulas.get('GEOMETRIC_ACCURACY_SCORE')}",
        f"  NOTE: {result.formulas.get('disclaimer')}",
        "",
        "SUMMARY",
        f"Total CONTROL (listed): {len(result.points)}",
        f"Confirmed: {result.confirmed}",
        f"Expected only: {result.expected_only}",
        f"Excluded: {result.excluded}",
        f"Matched: {result.matched}",
        f"Mismatched: {result.mismatched}",
        f"Point Match Rate: {_pct(result.point_match_rate)}",
        f"Geometric Score: {_pct(result.geometric_score)}",
        f"Mean error: {_n(result.mean_error)} {result.unit}",
        f"Median error: {_n(result.median_error)} {result.unit}",
        f"RMSE: {_n(result.rmse)} {result.unit}",
        f"Maximum error: {_n(result.max_error)} {result.unit}",
        f"Mean DX: {_n(result.mean_dx)} {result.unit}",
        f"Mean DY: {_n(result.mean_dy)} {result.unit}",
        "",
        "GROUP RESULTS",
    ]
    if not result.groups:
        lines.append("(none)")
    for g in result.groups:
        lines.append(
            f"{g['group']}: {g['matched']}/{g['total']} MATCH  "
            f"{_pct(g.get('point_match_rate'))}  RMSE {_n(g.get('rmse'))}  MAX {_n(g.get('max'))}"
        )
    lines += ["", "POINT RESULTS"]
    for p in sorted(result.points, key=lambda x: (-(x.error if x.included else -1), x.id)):
        lines += [
            "--------------------------------------------------",
            p.id,
            f"GROUP: {p.group}",
            f"SOURCE SIDE: {p.source_side}",
            f"SOURCE: X = {p.source_x:.4f} {p.unit}   Y = {p.source_y:.4f} {p.unit}",
            f"EXPECTED: X = {p.expected_x:.4f} {p.unit}   Y = {p.expected_y:.4f} {p.unit}",
            f"ACTUAL: X = {p.actual_x:.4f} {p.unit}   Y = {p.actual_y:.4f} {p.unit}",
            f"DX = {p.dx:+.4f} {p.unit}",
            f"DY = {p.dy:+.4f} {p.unit}",
            f"ERROR = {p.error:.4f} {p.unit}",
            f"TOLERANCE = {p.tolerance:.4f} {p.unit}" if p.tolerance is not None else "TOLERANCE = NOT AVAILABLE — MM SCALE NOT SET",
            f"TOLERANCE APPLIED: {p.tolerance_applied or result.tolerance_label}",
            f"STATUS = {p.status}" + (f"  ({p.exclude_reason})" if p.exclude_reason else ""),
            "--------------------------------------------------",
        ]
    lines += ["", "MESH EDGE RESULTS (EXPECTED B vs ACTUAL B)", f"Edge count: {len(result.mesh_edges)}"]
    for e in result.mesh_edges:
        lines.append(
            f"{e.get('POINT_A')}–{e.get('POINT_B')}: "
            f"EXPECTED_B={e.get('LENGTH_EXPECTED_B', e.get('LENGTH_A'))} "
            f"ACTUAL_B={e.get('LENGTH_ACTUAL_B', e.get('LENGTH_B'))} "
            f"Δ={e.get('DELTA')} ({e.get('PERCENT_DELTA')}%)"
        )
    lines += ["", "MESH TRIANGLE RESULTS", f"Triangle count: {len(result.mesh_triangles)}"]
    for t in result.mesh_triangles:
        lines.append(
            f"{t.get('P1')}-{t.get('P2')}-{t.get('P3')}: "
            f"area EXPECTED/ACTUAL={t.get('AREA_A')}/{t.get('AREA_B')} "
            f"scale={t.get('LOCAL_SCALE')} rot={t.get('LOCAL_ROTATION')} shape={t.get('SHAPE_ERROR')}"
        )
    lines += ["", "CONTOUR RESULTS"]
    if not result.contours:
        lines.append("(none)")
    for c in result.contours:
        lines.append(
            f"{c.get('ID')}  SIDE={c.get('SIDE')}  ROLE={c.get('ROLE')}  "
            f"MEAN={c.get('MEAN')}  MEDIAN={c.get('MEDIAN')}  RMSE={c.get('RMSE')}  "
            f"MAX={c.get('MAX')}  STATUS={c.get('STATUS')}"
        )
    lines += ["", "DIMENSION RESULTS"]
    if not result.dimensions:
        lines.append("(none)")
    for d in result.dimensions:
        lines.append(
            f"{d.get('ID')}  TYPE={d.get('TYPE')}  P1={d.get('P1')}  P2={d.get('P2')}  "
            f"A_LENGTH={d.get('A_LENGTH')}  B_LENGTH={d.get('B_LENGTH')}  "
            f"DELTA={d.get('DELTA')}  PERCENT={d.get('PERCENT_DELTA')}  "
            f"TOLERANCE={d.get('TOLERANCE')}  STATUS={d.get('STATUS')}"
        )
    lines += ["", "DIAGNOSTICS"]
    if not result.diagnostics:
        lines.append("No diagnostic suspicions.")
    for d in result.diagnostics:
        lines.append(f"{d.get('flag')}: {d.get('message')}  evidence={d.get('evidence')}")
        lines.append("Diagnostic only — not auto-corrected.")
    lines += ["", "UNVERIFIED / EXCLUDED ITEMS"]
    excl = [p for p in result.points if not p.included]
    if not excl:
        lines.append("(none)")
    for p in excl:
        lines.append(f"{p.id}: {p.status} {p.exclude_reason}".rstrip())
    return "\n".join(lines) + "\n"


def _fmt_adj(adj) -> str:
    if not isinstance(adj, dict):
        return str(adj or "—")
    return (
        f"Brightness {adj.get('brightness', 0)}  "
        f"Contrast {adj.get('contrast', 0):+}  "
        f"Sharpness {adj.get('sharpness', 0):+}  "
        f"Gamma {adj.get('gamma', 1.0):.2f}  "
        f"Saturation {adj.get('saturation', 0)}"
    )


def _fmt_info(info) -> str:
    if not isinstance(info, dict):
        return str(info or "—")
    return (
        f"{info.get('width', '?')}×{info.get('height', '?')} "
        f"{info.get('format', '')} {info.get('mode', '')} "
        f"{info.get('source_bytes', 0)} bytes"
    )


def _fmt_group_tols(groups: dict) -> list[str]:
    if not groups:
        return ["GROUP TOLERANCES: (none — all inherit GLOBAL)"]
    lines = ["GROUP TOLERANCES:"]
    for name, val in sorted(groups.items()):
        lines.append(f"  {name} = {val}")
    return lines


def _pct(v) -> str:
    return "—" if v is None else f"{float(v):.2f}%"


def _n(v) -> str:
    return "—" if v is None else f"{float(v):.4f}"
