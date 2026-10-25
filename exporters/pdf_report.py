"""Professional PDF report. MEASURED / DERIVED / UNVERIFIED are separate sections."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path

from models.enums import Confidence, FactClass, PointType


class PdfReportError(ValueError):
    pass


def generate_pdf(project, path: str | Path, *, overlay_png: bytes | None = None, extra_images: dict | None = None) -> None:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            Image,
            PageBreak,
            Paragraph,
            Preformatted,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as exc:
        raise PdfReportError("reportlab is not installed. Run: pip install reportlab") from exc

    path = Path(path)
    styles = getSampleStyleSheet()
    story = []
    story.append(Paragraph("PCB Image Comparator — Verification Report", styles["Title"]))
    story.append(Paragraph(project.name, styles["Heading2"]))
    story.append(Paragraph(datetime.now().strftime("%Y-%m-%d %H:%M"), styles["Normal"]))
    story.append(Spacer(1, 8))

    def p(text, style="Normal"):
        story.append(Paragraph(text.replace("\n", "<br/>"), styles[style]))

    p("This report never presents inferred or cropped features as measured.", "Italic")
    story.append(Spacer(1, 10))

    p("Images", "Heading2")
    for label, img in (("A", project.image_a), ("B", project.image_b)):
        if img is None:
            p(f"Image {label}: not loaded")
            continue
        cal = img.calibration
        scale = f"{cal.mm_per_px:.6f} mm/px" if cal.is_calibrated else "not calibrated"
        p(
            f"Image {label}: {img.width}×{img.height}px  source={img.source_path or img.embedded_name}  "
            f"PCB side={img.pcb_side.value}  scale={scale}  derived={img.is_derived}  notes={img.notes}"
        )

    p("Registration", "Heading2")
    rec = project.current_transform
    if rec is None:
        p("No transform accepted.")
    else:
        t = rec.transform
        p(
            f"Mode={rec.mode.value}  Status={rec.status.value}  Orientation B={rec.orientation_b.name.value}  "
            f"Scale={t.scale:.6f}  Rotation={t.rotation_deg:.4f}°  TX={t.tx:.4f}px  TY={t.ty:.4f}px"
        )
        if rec.summary:
            p(
                f"Reference count={rec.summary.get('count')}  RMSE={rec.summary.get('rmse')}  "
                f"mean={rec.summary.get('mean')}  max={rec.summary.get('max')}"
            )
        if rec.affine:
            p("AFFINE (DIAGNOSTIC ONLY, not physical proof): " + str(rec.affine), "Italic")

    def table(title, headers, rows, fact: FactClass | None = None):
        if fact:
            p(f"{title} — {fact.value}", "Heading2")
        else:
            p(title, "Heading2")
        data = [headers] + rows
        tbl = Table(data, repeatRows=1)
        tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTSIZE", (0, 0), (-1, -1), 7),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6f7")]),
                ]
            )
        )
        story.append(tbl)
        story.append(Spacer(1, 8))

    measured = []
    derived = []
    unverified = []
    xf = rec.transform if rec else None
    for lm in project.landmarks:
        pred = ""
        err = ""
        if xf and lm.has_a:
            px, py = xf.apply_one(lm.a_x, lm.a_y)
            pred = f"{px:.3f},{py:.3f}"
            if lm.has_b:
                err = f"{((lm.b_x - px) ** 2 + (lm.b_y - py) ** 2) ** 0.5:.4f}"
        row = [
            lm.id,
            lm.point_type.value,
            lm.group,
            f"{lm.a_x:.3f}" if lm.a_x is not None else "",
            f"{lm.a_y:.3f}" if lm.a_y is not None else "",
            pred,
            f"{lm.b_x:.3f}" if lm.b_x is not None else "",
            f"{lm.b_y:.3f}" if lm.b_y is not None else "",
            err,
            lm.confidence.value,
            lm.notes,
        ]
        if lm.confidence in (Confidence.INFERRED,):
            unverified.append(row)
        elif lm.confidence in (Confidence.UNVERIFIED, Confidence.CROPPED, Confidence.PARTIAL, Confidence.PARTIALLY_VISIBLE):
            unverified.append(row)
        elif lm.point_type == PointType.MEASUREMENT and lm.has_a and lm.has_b:
            measured.append(row)
        elif lm.point_type == PointType.REFERENCE and lm.has_a and lm.has_b:
            measured.append(row)
        else:
            derived.append(row)

    headers = ["ID", "TYPE", "GROUP", "A_X", "A_Y", "B_PRED", "B_ACT_X", "B_ACT_Y", "ERROR_PX", "CONF", "NOTES"]
    table("MEASURED features", headers, measured or [["—"] * len(headers)], FactClass.MEASURED)
    table("DERIVED / incomplete correspondences", headers, derived or [["—"] * len(headers)], FactClass.DERIVED)
    table("UNVERIFIED / cropped / inferred", headers, unverified or [["—"] * len(headers)], FactClass.UNVERIFIED)

    p("Groups", "Heading2")
    for g in project.groups:
        p(f"{g.name}: result={g.result.value} flags={','.join(g.flags) or 'none'} notes={g.notes}")

    p("Contours", "Heading2")
    for c in project.contours:
        p(f"{c.id} {c.side.value} {c.label} vertices={len(c.vertices)} closed={c.closed} source={c.source}")

    p("Largest deviations", "Heading2")
    errors = []
    if xf:
        for lm in project.landmarks:
            if lm.has_a and lm.has_b:
                px, py = xf.apply_one(lm.a_x, lm.a_y)
                e = ((lm.b_x - px) ** 2 + (lm.b_y - py) ** 2) ** 0.5
                errors.append((e, lm.id, lm.point_type.value, lm.confidence.value))
        errors.sort(reverse=True)
        for e, i, t, c in errors[:15]:
            p(f"{i} ({t}, {c}): {e:.4f} px")

    p("Lens distortion", "Heading2")
    p(project.lens_distortion or "UNKNOWN — no camera calibration is present.")

    p("Notes", "Heading2")
    p(project.notes or "—")

    p("Audit trail", "Heading2")
    for ev in project.audit[-40:]:
        p(f"{ev.timestamp}  {ev.action}  {ev.detail}")

    if overlay_png:
        story.append(PageBreak())
        p("Overlay (appearance only — not geometric proof)", "Heading2")
        bio = BytesIO(overlay_png)
        story.append(Image(bio, width=170 * mm, height=100 * mm, kind="proportional"))
    if extra_images:
        for title, blob in extra_images.items():
            story.append(PageBreak())
            p(title, "Heading2")
            story.append(Image(BytesIO(blob), width=170 * mm, height=100 * mm, kind="proportional"))

    doc = SimpleDocTemplate(str(path), pagesize=A4, title=f"PCB Comparator — {project.name}")
    doc.build(story)
