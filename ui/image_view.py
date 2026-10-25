"""Interactive QGraphicsView canvas. Coordinates stay in original/oriented space."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QCursor, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QTransform, QWheelEvent
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QMenu,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from models.enums import PointType, Side
from ui.magnifier import Magnifier


class ControlMarkerItem(QGraphicsItem):
    """Original large scene-space hollow CONTROL ring + exact center cross.

    Ring radius is the pre-regression value from add_point_marker (r = 8 scene px,
    diameter 16). It scales with zoom. The small + sits at CONTROL.x / CONTROL.y.
    """

    RING_R = 8.0
    CROSS_HALF = 2.5
    DOT_R = 0.55

    def __init__(
        self,
        color: QColor,
        *,
        dashed: bool = False,
        selected: bool = False,
        primary: bool = False,
        pulse: bool = False,
    ) -> None:
        super().__init__()
        self.ring_r = float(self.RING_R)
        self.ring_d = float(self.RING_R * 2.0)
        self.cross = float(self.CROSS_HALF)
        self.color = QColor(color)
        self.dashed = bool(dashed)
        self.selected = bool(selected)
        self.primary = bool(primary)
        self.pulse = bool(pulse)
        self.setZValue(22 if selected else 20)

    def boundingRect(self) -> QRectF:  # noqa: N802
        r = self.ring_r + 14.0
        return QRectF(-r, -r, 2 * r, 2 * r)

    def paint(self, painter, _option, _widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self.selected:
            t = painter.transform()
            inv = max(abs(t.m11()), 1e-6)
            gap = (6.0 if self.primary else 4.0) / inv
            halo = QPen(QColor(255, 255, 255, 240 if self.pulse or self.primary else 190), 2.2 if self.primary else 1.6)
            halo.setCosmetic(True)
            painter.setPen(halo)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(0, 0), self.ring_r + gap, self.ring_r + gap)
        pen = QPen(self.color, 3.0 if self.selected else 2.0)
        if self.dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(0, 0), self.ring_r, self.ring_r)
        cpen = QPen(self.color, 1.0)
        painter.setPen(cpen)
        c = self.cross
        painter.drawLine(QPointF(-c, 0), QPointF(c, 0))
        painter.drawLine(QPointF(0, -c), QPointF(0, c))
        painter.setBrush(self.color)
        painter.drawEllipse(QPointF(0, 0), self.DOT_R, self.DOT_R)


class CropHud(QWidget):
    """Floating KEEP / CANCEL / RESET — nothing is applied until KEEP THIS AREA."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            "QWidget { background: rgba(20, 22, 26, 210); border: 1px solid #f1c40f; border-radius: 4px; }"
            "QPushButton { padding: 4px 10px; font-weight: 600; }"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(6)
        self.keep_btn = QPushButton()
        self.cancel_btn = QPushButton()
        self.reset_btn = QPushButton()
        self.keep_btn.setStyleSheet("QPushButton { background:#f1c40f; color:#111; }")
        row.addWidget(self.keep_btn)
        row.addWidget(self.cancel_btn)
        row.addWidget(self.reset_btn)
        self.retranslate()

    def retranslate(self) -> None:
        from app.i18n import tr

        self.keep_btn.setText(tr("crop.keep"))
        self.cancel_btn.setText(tr("crop.cancel"))
        self.reset_btn.setText(tr("crop.reset"))


def numpy_to_qimage(bgr) -> QImage:
    """Aligned Format_RGB32. Format_RGB888 with stride 3*w smears rows when w*3 % 4 != 0."""
    import numpy as np

    arr = np.ascontiguousarray(bgr)
    if arr.ndim == 2:
        h, w = arr.shape
        rgb32 = np.empty((h, w, 4), dtype=np.uint8)
        rgb32[:, :, 0] = arr
        rgb32[:, :, 1] = arr
        rgb32[:, :, 2] = arr
        rgb32[:, :, 3] = 255
    else:
        h, w = arr.shape[:2]
        rgb32 = np.empty((h, w, 4), dtype=np.uint8)
        # Qt Format_RGB32 is BGRA in memory on little-endian.
        rgb32[:, :, 0] = arr[:, :, 0]
        rgb32[:, :, 1] = arr[:, :, 1]
        rgb32[:, :, 2] = arr[:, :, 2]
        rgb32[:, :, 3] = 255
    qimg = QImage(rgb32.data, w, h, 4 * w, QImage.Format.Format_RGB32)
    return qimg.copy()


def _array_key(array) -> tuple:
    h, w = array.shape[:2]
    ptr = int(array.ctypes.data) if hasattr(array, "ctypes") else id(array)
    sample = 0
    if array.size:
        sample = int(array.flat[0]) + int(array.flat[min(array.size - 1, w * 3 + 1)])
    return (w, h, ptr, sample)


class ImageView(QGraphicsView):
    pointClicked = Signal(float, float)
    cursorMoved = Signal(float, float)
    viewChanged = Signal()
    residualClicked = Signal(str)
    requestNudge = Signal(float, float)
    contextCommand = Signal(str, float, float)
    filesDropped = Signal(list)
    imageDropped = Signal(object)
    previewMoved = Signal(float, float)
    releasedAt = Signal(float, float, bool)
    dragMoved = Signal(float, float, float, float)
    doubleClickedAt = Signal(float, float)

    def __init__(self, side: Side, parent=None) -> None:
        super().__init__(parent)
        self.side = side
        self.scene_obj = QGraphicsScene(self)
        self.scene_obj.setBackgroundBrush(Qt.BrushStyle.NoBrush)
        self.setScene(self.scene_obj)
        self.setBackgroundBrush(QColor(26, 26, 26))
        if self.viewport() is not None:
            self.viewport().setAutoFillBackground(True)
            pal = self.viewport().palette()
            pal.setColor(self.viewport().backgroundRole(), QColor(26, 26, 26))
            self.viewport().setPalette(pal)
        # Vectors may use AA. Pixmap interpolation is chosen only in _apply_interpolation.
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(80, 80)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self._space_pan = False
        self._middle_pan = False
        self._right_pan = False
        self._right_dragged = False
        self._right_press = None
        self._last_pan = None
        self._saved_cursor = None
        self._has_image = False
        self._pix_item: QGraphicsPixmapItem | None = None
        self._pixmap = QPixmap()
        self._overlay_items: list = []
        self._vector_items: list = []
        self.crosshair = True
        self.show_magnifier = False
        self.magnifier = Magnifier(self)
        self.magnifier.hide()
        self._cursor = QPointF()
        self._colors = {
            "zero": QColor("#e74c3c"),
            "scale": QColor("#2980b9"),
            "control": QColor("#27ae60"),
            "reference": QColor("#e74c3c"),
            "measurement": QColor("#27ae60"),
            "predicted": QColor("#2ecc71"),
            "residual": QColor("#e67e22"),
            "mesh_expected": QColor("#7f8c8d"),
            "mesh_actual": QColor("#27ae60"),
            "contour_a": QColor("#3498db"),
            "contour_b": QColor("#9b59b6"),
        }
        self._title = QGraphicsSimpleTextItem(f"MODEL {side.value}")
        self._title.setBrush(QColor("#ecf0f1"))
        self.scene_obj.addItem(self._title)
        self.pixel_precise = False
        self._source_array = None
        self._original_array = None
        self._pix_key = None
        self._layer_item: QGraphicsPixmapItem | None = None
        self._layer_key = None
        self._peer_cursor: QPointF | None = None
        self._qimage_size = (0, 0)
        self._qimage: QImage | None = None
        self._source_pixmap = QPixmap()
        self._hidpi_key = None
        self._hidpi_pix = QPixmap()
        self._crop_rect = None
        self._preview_items: list = []
        self.render_mode = "auto"
        self._press_scene = None
        self._dragging_draw = False
        self.contour_line_width = 1.5
        self.contour_node_px = 3.0
        self.contour_selected_node_px = 6.0
        self.contour_handle_px = 4.0
        self.precision_drawing = False
        self.point_marker_px = 12.0
        self.center_cross_px = 6.0
        self.point_label_px = 11.0
        self.precision_points = False
        self.magnifier_from_original = True
        self._crop_draft_rect = None
        self._crop_clip = None
        self._interp_mode = "nearest"
        # Parent to the viewport so KEEP / CANCEL / RESET sit above the scene and receive clicks.
        self.crop_hud = CropHud(self.viewport())
        self.crop_hud.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.crop_hud.hide()

    def set_marker_colors(self, colors: dict[str, str]) -> None:
        for k, v in colors.items():
            self._colors[k] = QColor(v)

    def set_image_array(self, array, *, reset_view: bool = False) -> None:
        keep = None
        center = None
        if self._has_image and not reset_view:
            keep = self.transform()
            center = self.mapToScene(self.viewport().rect().center())
        if array is None:
            self._clear_image()
            self._has_image = False
            self._source_array = None
            self._original_array = None
            self._pix_key = None
            self._qimage_size = (0, 0)
            self._qimage = None
            self._hidpi_key = None
            return
        key = _array_key(array)
        if key == self._pix_key and self._pix_item is not None:
            if reset_view:
                self.fit_image()
            else:
                self._apply_interpolation()
            return
        self._clear_image()
        self._source_array = array
        if self._original_array is None:
            self._original_array = array
        h, w = array.shape[:2]
        # Full-resolution scene image. The VIEW is scaled; the IMAGE is not.
        qimg = numpy_to_qimage(array)
        self._qimage = qimg
        self._qimage_size = (qimg.width(), qimg.height())
        self._source_pixmap = QPixmap.fromImage(qimg)
        self._source_pixmap.setDevicePixelRatio(1.0)
        self._pixmap = self._source_pixmap
        self._hidpi_key = None
        self._pix_item = QGraphicsPixmapItem(self._source_pixmap)
        self._pix_item.setTransformationMode(Qt.TransformationMode.FastTransformation)
        self._pix_item.setCacheMode(QGraphicsItem.CacheMode.NoCache)
        self._pix_item.setZValue(-10)
        self.scene_obj.addItem(self._pix_item)
        self.lock_scene_to_image()
        self._place_title()
        if self._crop_rect is not None:
            self.set_working_region(self._crop_rect, fit=False)
        self._pix_key = key
        self._apply_interpolation()
        first = not self._has_image
        self._has_image = True
        if keep is not None and not first:
            self.setTransform(keep)
            if center is not None:
                self.centerOn(center)
        else:
            self.fit_image()

    def set_original_array(self, array) -> None:
        """Full-resolution original pixels for the magnifier / quality compare."""
        self._original_array = array

    def _clear_image(self) -> None:
        if self._pix_item is not None:
            self._pix_item.setParentItem(None)
            self.scene_obj.removeItem(self._pix_item)
            self._pix_item = None
        self.clear_registered_layer()
        self._detach_crop_clip()

    def clear_overlays(self) -> None:
        for it in self._overlay_items:
            self.scene_obj.removeItem(it)
        self._overlay_items.clear()
        self.lock_scene_to_image()

    def clear_vectors(self) -> None:
        for it in self._vector_items:
            self.scene_obj.removeItem(it)
        self._vector_items.clear()

    def add_point_marker(
        self,
        x: float,
        y: float,
        label: str,
        kind: PointType | str,
        *,
        predicted: bool = False,
        selected: bool = False,
        hovered: bool = False,
        state: str = "",
        show_label: bool = True,
        label_offset: tuple[float, float] | None = None,
        payload: str | None = None,
        primary: bool = False,
        pulse: bool = False,
    ) -> None:
        """Original large hollow ring (r=8 scene px) + exact center cross at (x, y)."""
        kind_s = kind.value if isinstance(kind, PointType) else str(kind)
        if kind_s == "ZERO" or (kind_s == "REFERENCE" and not predicted):
            color = self._colors.get("zero", QColor("#e74c3c"))
            dashed = False
        elif kind_s == "SCALE":
            color = self._colors.get("scale", QColor("#2980b9"))
            dashed = False
        elif predicted or state == "EXPECTED_ONLY":
            color = self._colors.get("predicted", QColor("#2ecc71"))
            dashed = True
        elif state == "MISMATCH":
            color = QColor("#c0392b")
            dashed = False
        elif state == "UNVERIFIED":
            color = QColor("#95a5a6")
            dashed = True
        else:
            color = self._colors.get("control", QColor("#27ae60"))
            dashed = False
        marker = ControlMarkerItem(
            color, dashed=dashed, selected=selected, primary=primary, pulse=pulse
        )
        marker.setPos(float(x), float(y))
        if payload:
            marker.setData(0, payload)
        self.scene_obj.addItem(marker)
        self._overlay_items.append(marker)
        if show_label and label:
            text = QGraphicsSimpleTextItem(label)
            text.setBrush(color)
            font = text.font()
            font.setPixelSize(max(8, int(getattr(self, "point_label_px", 11.0) or 11)))
            text.setFont(font)
            rad = ControlMarkerItem.RING_R
            ox, oy = (rad + 3.0, -rad - 2.0) if label_offset is None else label_offset
            if selected:
                font.setBold(True)
                font.setPixelSize(max(10, int(getattr(self, "point_label_px", 11.0) or 11) + 1))
                text.setFont(font)
                text.setPos(float(x), float(y))
                text.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
                text.moveBy(12.0, -14.0)
            else:
                text.setPos(float(x) + float(ox), float(y) + float(oy))
            text.setZValue(23 if selected else 22)
            if payload:
                text.setData(0, payload)
            self.scene_obj.addItem(text)
            self._overlay_items.append(text)

    def _cross(self, x, y, r, color):
        p1 = QGraphicsLineItem(x - r, y, x + r, y)
        p2 = QGraphicsLineItem(x, y - r, x, y + r)
        for p in (p1, p2):
            p.setPen(QPen(color, 3))
        return [p1, p2]

    def _diamond(self, x, y, r, color, fill=True):
        poly = QPolygonF([QPointF(x, y - r), QPointF(x + r, y), QPointF(x, y + r), QPointF(x - r, y)])
        item = QGraphicsPolygonItem(poly)
        item.setPen(QPen(color, 2))
        item.setBrush(QColor(color.red(), color.green(), color.blue(), 160) if fill else Qt.BrushStyle.NoBrush)
        return item

    def _triangle(self, x, y, r, color):
        poly = QPolygonF([QPointF(x, y - r), QPointF(x + r, y + r), QPointF(x - r, y + r)])
        item = QGraphicsPolygonItem(poly)
        item.setPen(QPen(color, 2))
        item.setBrush(QColor(color.red(), color.green(), color.blue(), 180))
        return item

    def add_tolerance_circle(self, x: float, y: float, radius: float, *, match: bool = True) -> None:
        """Scene-unit circle — zooms with the image, not a fixed screen radius."""
        if radius <= 0:
            return
        el = QGraphicsEllipseItem(x - radius, y - radius, 2 * radius, 2 * radius)
        color = QColor("#27ae60") if match else QColor("#c0392b")
        pen = QPen(color, 0)
        pen.setCosmetic(False)
        pen.setWidthF(max(0.5, radius * 0.04))
        el.setPen(pen)
        el.setBrush(Qt.BrushStyle.NoBrush)
        el.setZValue(12)
        self.scene_obj.addItem(el)
        self._overlay_items.append(el)

    def set_pixel_precise(self, on: bool) -> None:
        if self.pixel_precise == bool(on):
            self._apply_interpolation()
            return
        self.pixel_precise = bool(on)
        if self._source_array is not None:
            self.set_image_array(self._source_array, reset_view=False)
        self._apply_interpolation()

    def _device_dpr(self) -> float:
        vp = self.viewport()
        if vp is None:
            return 1.0
        return float(vp.devicePixelRatioF() or 1.0)

    def _apply_interpolation(self) -> None:
        zoom = abs(self.current_zoom())
        if self.render_mode == "inspect" or self.pixel_precise:
            downscaling = False
        elif self.render_mode == "source_1to1":
            downscaling = False
        elif self.render_mode == "fit_hq":
            downscaling = zoom < 0.999
        else:
            downscaling = zoom < 0.999 and not self.pixel_precise
        # One intentional resample: Smooth only when shrinking FROM ORIGINAL. ≥100% nearest.
        mode = Qt.TransformationMode.SmoothTransformation if downscaling else Qt.TransformationMode.FastTransformation
        self._interp_mode = "smooth" if downscaling else "nearest"
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, downscaling)
        # Antialiasing on a pixmap at ≥1× is a common HiDPI softness source.
        self.setRenderHint(QPainter.RenderHint.Antialiasing, downscaling)
        dpr = self._device_dpr()
        if self._pix_item is not None and self._qimage is not None and not self._source_pixmap.isNull():
            # Do not keep a Smooth-scaled item cache — Fit then 1:1 must re-fetch original.
            self._pix_item.setCacheMode(QGraphicsItem.CacheMode.NoCache)
            if downscaling or dpr <= 1.01:
                self._pix_item.setPixmap(self._source_pixmap)
                self._pixmap = self._source_pixmap
            else:
                # HiDPI: present a nearest-expanded pixmap so Qt does not bilinear-upsample
                # a 1:1 logical pixmap onto a 1.25× / 1.5× device framebuffer.
                tw = max(1, int(round(self._qimage_size[0] * dpr)))
                th = max(1, int(round(self._qimage_size[1] * dpr)))
                key = (tw, th, round(dpr, 4))
                if self._hidpi_key != key:
                    scaled = self._qimage.scaled(
                        tw,
                        th,
                        Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.FastTransformation,
                    )
                    pix = QPixmap.fromImage(scaled)
                    pix.setDevicePixelRatio(dpr)
                    self._hidpi_pix = pix
                    self._hidpi_key = key
                self._pix_item.setPixmap(self._hidpi_pix)
                # Quality tests and the magnifier still use original pixels.
                self._pixmap = self._source_pixmap
        for item in (self._pix_item, self._layer_item):
            if item is not None:
                item.setTransformationMode(mode)
                item.setCacheMode(QGraphicsItem.CacheMode.NoCache)

    def set_registered_layer(self, array, qtransform: QTransform, opacity: float) -> None:
        """Full-resolution B (or A) layer. Transform + opacity only — no baked preview."""
        if array is None:
            self.clear_registered_layer()
            return
        key = (_array_key(array),)
        if self._layer_item is None or self._layer_key != key:
            if self._layer_item is not None:
                self.scene_obj.removeItem(self._layer_item)
            qimg = numpy_to_qimage(array)
            pix = QPixmap.fromImage(qimg)
            pix.setDevicePixelRatio(1.0)
            self._layer_item = QGraphicsPixmapItem(pix)
            self._layer_item.setZValue(-9)
            self.scene_obj.addItem(self._layer_item)
            self._layer_key = key
        self._layer_item.setTransform(qtransform)
        self._layer_item.setOpacity(max(0.0, min(1.0, float(opacity))))
        if self._crop_rect is not None:
            self._attach_crop_clip()
        self.lock_scene_to_image()
        self._apply_interpolation()

    def set_layer_opacity(self, opacity: float) -> None:
        if self._layer_item is not None:
            self._layer_item.setOpacity(max(0.0, min(1.0, float(opacity))))
            self.viewport().update()

    def set_base_opacity(self, opacity: float) -> None:
        self.set_image_opacity(opacity)

    def clear_registered_layer(self) -> None:
        if self._layer_item is not None:
            self._layer_item.setParentItem(None)
            self.scene_obj.removeItem(self._layer_item)
            self._layer_item = None
            self._layer_key = None

    def set_peer_cursor(self, xy: tuple[float, float] | None) -> None:
        self._peer_cursor = None if xy is None else QPointF(xy[0], xy[1])
        self.viewport().update()

    def quality_diag(self) -> dict:
        src = (0, 0) if self._source_array is None else (self._source_array.shape[1], self._source_array.shape[0])
        fmt = ""
        if self._source_array is not None:
            fmt = "L" if self._source_array.ndim == 2 else f"BGR{self._source_array.shape[2]}"
        pix = (self._pixmap.width(), self._pixmap.height()) if not self._pixmap.isNull() else (0, 0)
        item = (0, 0)
        if self._pix_item is not None:
            r = self._pix_item.boundingRect()
            item = (int(round(r.width())), int(round(r.height())))
        dpr = self._device_dpr()
        dpi = 96.0
        try:
            from PySide6.QtGui import QGuiApplication

            scr = QGuiApplication.primaryScreen()
            if scr is not None:
                dpi = float(scr.logicalDotsPerInch())
        except Exception:
            pass
        from_original = src == pix == self._qimage_size and src[0] > 0
        return {
            "source": src,
            "source_format": fmt,
            "decoded": src,
            "processed": src,
            "qimage": self._qimage_size,
            "pixmap": pix,
            "scene": item,
            "zoom": self.current_zoom(),
            "dpr": dpr,
            "windows_dpi": dpi,
            "interpolation": self._interp_mode,
            "from_original": from_original,
            "rendering_from_original": from_original,
            "source_1to1": abs(self.current_zoom() - 1.0) < 1e-4 and from_original and self._interp_mode == "nearest",
        }

    def add_residual(self, x0: float, y0: float, x1: float, y1: float, residual_id: str = "", *, mismatch: bool = False) -> None:
        line = QGraphicsLineItem(x0, y0, x1, y1)
        color = QColor("#c0392b") if mismatch else self._colors["residual"]
        line.setPen(QPen(color, 2.5 if mismatch else 2))
        line.setZValue(15)
        line.setData(0, residual_id)
        self.scene_obj.addItem(line)
        self._overlay_items.append(line)

    def add_mesh_segment(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        color_key: str,
        *,
        width: float = 1.5,
        alpha: int = 255,
        dashed: bool = False,
        payload: str = "",
        selected: bool = False,
    ) -> None:
        color = QColor(self._colors.get(color_key, QColor("#aaa")))
        color.setAlpha(alpha)
        line = QGraphicsLineItem(float(x0), float(y0), float(x1), float(y1))
        pen = QPen(color, width + (2.0 if selected else 0.0))
        pen.setCosmetic(True)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        line.setPen(pen)
        line.setZValue(16 if selected else 7)
        if selected:
            halo = QGraphicsLineItem(float(x0), float(y0), float(x1), float(y1))
            hpen = QPen(QColor(255, 255, 255, 220), 4.0)
            hpen.setCosmetic(True)
            halo.setPen(hpen)
            halo.setZValue(15)
            self.scene_obj.addItem(halo)
            self._overlay_items.append(halo)
        if payload:
            line.setData(0, payload)
        self.scene_obj.addItem(line)
        self._overlay_items.append(line)

    def set_image_opacity(self, opacity: float) -> None:
        if self._pix_item is not None:
            self._pix_item.setOpacity(max(0.0, min(1.0, opacity)))

    def add_polyline(self, points, color_key: str, closed: bool = False, width: float = 1.5) -> None:
        if len(points) < 2:
            return
        color = self._colors.get(color_key, QColor("#fff"))
        pts = list(points)
        if closed:
            pts = pts + [pts[0]]
        for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
            line = QGraphicsLineItem(float(x0), float(y0), float(x1), float(y1))
            line.setPen(QPen(color, width))
            line.setZValue(8)
            self.scene_obj.addItem(line)
            self._overlay_items.append(line)

    def add_vector_primitives(self, primitives, px_per_unit: float = 1.0) -> None:
        self.clear_vectors()
        color = QColor("#1abc9c")
        for prim in primitives:
            pts = [(p[0] * px_per_unit, p[1] * px_per_unit) for p in prim.points]
            if prim.kind == "circle" and prim.radius and pts:
                r = prim.radius * px_per_unit
                el = QGraphicsEllipseItem(pts[0][0] - r, pts[0][1] - r, 2 * r, 2 * r)
                el.setPen(QPen(color, 1))
                el.setZValue(5)
                self.scene_obj.addItem(el)
                self._vector_items.append(el)
                continue
            if len(pts) < 2:
                if pts:
                    el = QGraphicsEllipseItem(pts[0][0] - 2, pts[0][1] - 2, 4, 4)
                    el.setPen(QPen(color, 1))
                    self.scene_obj.addItem(el)
                    self._vector_items.append(el)
                continue
            seq = pts + ([pts[0]] if prim.closed else [])
            for (x0, y0), (x1, y1) in zip(seq[:-1], seq[1:]):
                line = QGraphicsLineItem(x0, y0, x1, y1)
                line.setPen(QPen(color, 1))
                line.setZValue(5)
                self.scene_obj.addItem(line)
                self._vector_items.append(line)

    def image_bounds(self) -> QRectF:
        """Authoritative image / active-crop rectangle. Overlays must not enlarge this."""
        if self._crop_rect is not None and self._crop_rect[2] >= 1 and self._crop_rect[3] >= 1:
            x, y, w, h = self._crop_rect
            return QRectF(float(x), float(y), float(w), float(h))
        if self._source_array is not None:
            h, w = self._source_array.shape[:2]
            return QRectF(0.0, 0.0, float(w), float(h))
        if self._pix_item is not None:
            return QRectF(self._pix_item.boundingRect())
        return QRectF()

    def lock_scene_to_image(self) -> None:
        """Pin scene + view rects to the real image. Decorations cannot grow Fit."""
        r = self.image_bounds()
        if r.isEmpty():
            return
        self.scene_obj.setSceneRect(r)
        self.setSceneRect(r)

    def bounds_diag(self) -> dict:
        r = self.image_bounds()
        sr = self.scene_obj.sceneRect()
        vr = self.sceneRect()
        br = self._pix_item.boundingRect() if self._pix_item is not None else QRectF()
        return {
            "source": (0, 0) if self._source_array is None else (int(self._source_array.shape[1]), int(self._source_array.shape[0])),
            "qimage": self._qimage_size,
            "pixmap": (self._pixmap.width(), self._pixmap.height()) if not self._pixmap.isNull() else (0, 0),
            "item": (br.width(), br.height()),
            "image_bounds": (r.x(), r.y(), r.width(), r.height()),
            "scene_rect": (sr.x(), sr.y(), sr.width(), sr.height()),
            "view_scene_rect": (vr.x(), vr.y(), vr.width(), vr.height()),
        }

    def fit_image(self) -> None:
        if self._pix_item is None:
            return
        self.lock_scene_to_image()
        r = self.image_bounds()
        if r.isEmpty():
            return
        self.fitInView(r, Qt.AspectRatioMode.KeepAspectRatio)
        self._apply_interpolation()
        self.viewChanged.emit()

    def center_keep_zoom(self, x: float, y: float) -> None:
        """Pan to (x, y) without changing the current zoom."""
        self.centerOn(float(x), float(y))
        self.viewChanged.emit()

    def zoom_100(self) -> None:
        """SOURCE 1:1 — original pixels, no Fit, no thumbnail, no leftover Smooth cache."""
        self.resetTransform()
        prev = self.render_mode
        self.render_mode = "source_1to1"
        self._hidpi_key = None
        if self._qimage is not None:
            self._source_pixmap = QPixmap.fromImage(self._qimage)
            self._source_pixmap.setDevicePixelRatio(1.0)
            self._pixmap = self._source_pixmap
        self._apply_interpolation()
        self.render_mode = prev
        if self._crop_rect is not None:
            x, y, cw, ch = self._crop_rect
            self.centerOn(float(x) + float(cw) / 2.0, float(y) + float(ch) / 2.0)
        self.viewChanged.emit()

    def set_crop_rect(self, rect) -> None:
        """View window in scene pixels. None restores the full scene."""
        self.set_working_region(rect, fit=False)

    def set_working_region(self, rect, *, fit: bool = False) -> None:
        """Clip the full-resolution pixmap to `rect` (scene/view pixels). No resample."""
        self._crop_rect = None if rect is None else (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
        h = self._source_array.shape[0] if self._source_array is not None else int(self.scene_obj.sceneRect().height() or 1)
        w = self._source_array.shape[1] if self._source_array is not None else int(self.scene_obj.sceneRect().width() or 1)
        if self._crop_rect is None or self._crop_rect[2] < 1 or self._crop_rect[3] < 1:
            self._detach_crop_clip()
            self.lock_scene_to_image()
            self._place_title()
            if fit:
                self.fit_image()
            else:
                self.viewport().update()
            return
        x, y, cw, ch = self._crop_rect
        self.lock_scene_to_image()
        self._attach_crop_clip()
        self._place_title()
        if fit:
            self.fit_image()
        else:
            self.viewport().update()

    def _place_title(self) -> None:
        if self._crop_rect is not None:
            x, y, _w, _h = self._crop_rect
            self._title.setPos(x + 8, y + 8)
        else:
            self._title.setPos(8, 8)

    def _attach_crop_clip(self) -> None:
        if self._crop_rect is None:
            return
        x, y, cw, ch = self._crop_rect
        if self._crop_clip is None:
            self._crop_clip = QGraphicsRectItem()
            self._crop_clip.setPen(QPen(Qt.PenStyle.NoPen))
            self._crop_clip.setBrush(Qt.BrushStyle.NoBrush)
            self._crop_clip.setZValue(-11)
            self._crop_clip.setFlag(QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape, True)
            self.scene_obj.addItem(self._crop_clip)
        self._crop_clip.setRect(x, y, cw, ch)
        if self._pix_item is not None and self._pix_item.parentItem() is not self._crop_clip:
            self._pix_item.setParentItem(self._crop_clip)
        if self._layer_item is not None and self._layer_item.parentItem() is not self._crop_clip:
            self._layer_item.setParentItem(self._crop_clip)

    def _detach_crop_clip(self) -> None:
        if self._pix_item is not None:
            self._pix_item.setParentItem(None)
        if self._layer_item is not None:
            self._layer_item.setParentItem(None)
        if self._crop_clip is not None:
            self.scene_obj.removeItem(self._crop_clip)
            self._crop_clip = None

    def working_region_rect(self):
        return self._crop_rect

    def quality_crop_compare(self, x0: int, y0: int, x1: int, y1: int) -> dict:
        """Compare QImage pixels to the source array in the same crop. Neutral path must match."""
        src = self._source_array
        if src is None or self._pixmap.isNull():
            return {"ok": False, "reason": "no image"}
        qimg = self._pixmap.toImage()
        mismatches = 0
        total = 0
        gray = src.ndim == 2
        for y in range(max(0, y0), min(src.shape[0], y1)):
            for x in range(max(0, x0), min(src.shape[1], x1)):
                total += 1
                c = qimg.pixelColor(x, y)
                if gray:
                    v = int(src[y, x])
                    if abs(c.red() - v) > 1 or abs(c.green() - v) > 1 or abs(c.blue() - v) > 1:
                        mismatches += 1
                else:
                    b, g, r = int(src[y, x, 0]), int(src[y, x, 1]), int(src[y, x, 2])
                    if abs(c.red() - r) > 1 or abs(c.green() - g) > 1 or abs(c.blue() - b) > 1:
                        mismatches += 1
        return {"ok": mismatches == 0, "mismatches": mismatches, "total": total, "source": src.shape[:2], "qimage": (qimg.width(), qimg.height())}

    def clear_preview(self) -> None:
        for it in self._preview_items:
            self.scene_obj.removeItem(it)
        self._preview_items.clear()

    def add_dimension_graphic(self, p1, p2, q1, q2, text_pos, text: str, *, preview: bool = False, color: QColor | None = None, selected: bool = False) -> None:
        col = color or QColor("#f1c40f")
        if preview:
            col = QColor("#f39c12")
        items = []
        if selected:
            for (a, b) in ((p1, q1), (p2, q2), (q1, q2)):
                halo = QGraphicsLineItem(float(a[0]), float(a[1]), float(b[0]), float(b[1]))
                hpen = QPen(QColor(255, 255, 255, 220), 4.0)
                hpen.setCosmetic(True)
                halo.setPen(hpen)
                items.append(halo)
        for (a, b) in ((p1, q1), (p2, q2)):
            ln = QGraphicsLineItem(float(a[0]), float(a[1]), float(b[0]), float(b[1]))
            ln.setPen(QPen(col, 0))
            pen = QPen(col, 2.0 if selected else 1)
            pen.setCosmetic(True)
            ln.setPen(pen)
            items.append(ln)
        dim = QGraphicsLineItem(float(q1[0]), float(q1[1]), float(q2[0]), float(q2[1]))
        dpen = QPen(col, 2.4 if selected else 1.5)
        dpen.setCosmetic(True)
        dim.setPen(dpen)
        items.append(dim)
        items.extend(self._arrowheads(q1, q2, col))
        label = QGraphicsSimpleTextItem(text)
        label.setBrush(col)
        if selected:
            font = label.font()
            font.setBold(True)
            label.setFont(font)
        label.setPos(float(text_pos[0]) + 4, float(text_pos[1]) - 12)
        items.append(label)
        bucket = self._preview_items if preview else self._overlay_items
        for it in items:
            it.setZValue(18)
            self.scene_obj.addItem(it)
            bucket.append(it)

    def _arrowheads(self, q1, q2, color):
        import math

        dx, dy = q2[0] - q1[0], q2[1] - q1[1]
        length = math.hypot(dx, dy) or 1.0
        ux, uy = dx / length, dy / length
        size = 8.0
        items = []
        for origin, sgn in ((q1, 1), (q2, -1)):
            bx, by = origin[0] + sgn * ux * size, origin[1] + sgn * uy * size
            px, py = -uy, ux
            poly = QPolygonF(
                [
                    QPointF(origin[0], origin[1]),
                    QPointF(bx + px * size * 0.4, by + py * size * 0.4),
                    QPointF(bx - px * size * 0.4, by - py * size * 0.4),
                ]
            )
            item = QGraphicsPolygonItem(poly)
            item.setPen(QPen(color, 1))
            item.setBrush(color)
            items.append(item)
        return items

    def add_contour_path(self, points, color_key: str, *, closed: bool = False, mismatch_mask=None, width: float | None = None, selected: bool = False) -> None:
        if len(points) < 2:
            return
        ok = QColor(self._colors.get(color_key, QColor("#3498db")))
        bad = QColor("#c0392b")
        pts = list(points)
        if closed:
            pts = pts + [pts[0]]
        lw = float(self.contour_line_width if width is None else width)
        if self.precision_drawing:
            lw = min(lw, 1.0)
        if selected:
            lw = max(lw, 2.4)
        for i, ((x0, y0), (x1, y1)) in enumerate(zip(pts[:-1], pts[1:])):
            if selected:
                halo = QGraphicsLineItem(float(x0), float(y0), float(x1), float(y1))
                hpen = QPen(QColor(255, 255, 255, 210), 4.0)
                hpen.setCosmetic(True)
                halo.setPen(hpen)
                halo.setZValue(8)
                self.scene_obj.addItem(halo)
                self._overlay_items.append(halo)
            line = QGraphicsLineItem(float(x0), float(y0), float(x1), float(y1))
            hot = bool(mismatch_mask is not None and i < len(mismatch_mask) and mismatch_mask[i])
            pen = QPen(bad if hot else ok, lw)
            pen.setCosmetic(True)
            line.setPen(pen)
            line.setZValue(10 if selected else 9)
            self.scene_obj.addItem(line)
            self._overlay_items.append(line)

    def add_handle(self, x: float, y: float, *, kind: str = "node", selected: bool = False) -> None:
        """Screen-space marker. The landmark stays an infinitesimal coordinate."""
        if kind == "handle":
            diameter = self.contour_handle_px
            color = QColor("#e67e22")
        elif selected:
            diameter = self.contour_selected_node_px
            color = QColor("#ffffff")
        else:
            diameter = self.contour_node_px
            color = QColor("#ecf0f1")
        if self.precision_drawing:
            diameter = min(diameter, 2.5 if kind == "node" and not selected else 4.0)
        r = max(0.6, float(diameter) / 2.0)
        el = QGraphicsEllipseItem(-r, -r, 2 * r, 2 * r)
        el.setPos(float(x), float(y))
        el.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        pen = QPen(color, 1)
        pen.setCosmetic(True)
        el.setPen(pen)
        el.setBrush(QBrush(color))
        el.setZValue(22)
        self.scene_obj.addItem(el)
        self._overlay_items.append(el)

    def add_crop_rect(self, x, y, w, h, *, preview: bool = False) -> None:
        self.set_working_crop_overlay(x, y, w, h, preview=preview)

    def set_working_crop_overlay(self, x, y, w, h, *, preview: bool = True) -> None:
        """Dim outside the working rectangle; keep inside at full brightness. No apply yet."""
        x, y, w, h = float(x), float(y), float(w), float(h)
        if w < 1 or h < 1:
            return
        self._crop_draft_rect = (x, y, w, h)
        src_w = self._source_array.shape[1] if self._source_array is not None else w + x
        src_h = self._source_array.shape[0] if self._source_array is not None else h + y
        dim = QColor(0, 0, 0, 150)
        path = QPainterPath()
        path.addRect(QRectF(0, 0, float(src_w), float(src_h)))
        hole = QPainterPath()
        hole.addRect(QRectF(x, y, w, h))
        path = path.subtracted(hole)
        shade = QGraphicsPathItem(path)
        shade.setBrush(QBrush(dim))
        shade.setPen(QPen(Qt.PenStyle.NoPen))
        shade.setZValue(15)
        shade.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.scene_obj.addItem(shade)
        bucket = self._preview_items if preview else self._overlay_items
        bucket.append(shade)
        rect = QGraphicsRectItem(x, y, w, h)
        pen = QPen(QColor("#f1c40f"), 1.5, Qt.PenStyle.SolidLine)
        pen.setCosmetic(True)
        rect.setPen(pen)
        rect.setBrush(Qt.BrushStyle.NoBrush)
        rect.setZValue(17)
        self.scene_obj.addItem(rect)
        bucket.append(rect)
        for hx, hy in self._crop_handle_points(x, y, w, h):
            handle = QGraphicsEllipseItem(-4, -4, 8, 8)
            handle.setPos(hx, hy)
            handle.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
            hpen = QPen(QColor("#f1c40f"), 1)
            hpen.setCosmetic(True)
            handle.setPen(hpen)
            handle.setBrush(QBrush(QColor("#f1c40f")))
            handle.setZValue(18)
            self.scene_obj.addItem(handle)
            bucket.append(handle)
        self._place_crop_hud()

    @staticmethod
    def _crop_handle_points(x, y, w, h):
        return (
            (x, y),
            (x + w / 2, y),
            (x + w, y),
            (x + w, y + h / 2),
            (x + w, y + h),
            (x + w / 2, y + h),
            (x, y + h),
            (x, y + h / 2),
        )

    def crop_body_at(self, sx: float, sy: float) -> bool:
        if not self._crop_draft_rect or self.crop_handle_at(sx, sy):
            return False
        x, y, w, h = self._crop_draft_rect
        return x <= sx <= x + w and y <= sy <= y + h

    def move_crop_draft(self, nx: float, ny: float, w: float, h: float) -> tuple[float, float, float, float]:
        self._crop_draft_rect = (float(nx), float(ny), float(w), float(h))
        return self._crop_draft_rect

    def crop_handle_at(self, sx: float, sy: float) -> str | None:
        if not self._crop_draft_rect:
            return None
        x, y, w, h = self._crop_draft_rect
        names = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
        hit = 8.0 / max(abs(self.current_zoom()), 1e-6)
        for name, (hx, hy) in zip(names, self._crop_handle_points(x, y, w, h)):
            if abs(sx - hx) <= hit and abs(sy - hy) <= hit:
                return name
        return None

    def resize_crop_draft(self, handle: str, sx: float, sy: float) -> tuple[float, float, float, float] | None:
        if not self._crop_draft_rect:
            return None
        x, y, w, h = self._crop_draft_rect
        x2, y2 = x + w, y + h
        if "w" in handle:
            x = sx
        if "e" in handle:
            x2 = sx
        if "n" in handle:
            y = sy
        if "s" in handle:
            y2 = sy
        nx, ny = min(x, x2), min(y, y2)
        nw, nh = abs(x2 - x), abs(y2 - y)
        self._crop_draft_rect = (nx, ny, nw, nh)
        return self._crop_draft_rect

    def _place_crop_hud(self) -> None:
        self.crop_hud.adjustSize()
        vw = self.viewport().width()
        self.crop_hud.move(max(8, (vw - self.crop_hud.width()) // 2), 8)
        self.crop_hud.show()
        self.crop_hud.raise_()

    def hide_crop_hud(self) -> None:
        self.crop_hud.hide()
        self._crop_draft_rect = None

    def reset_view(self) -> None:
        """Reset zoom and pan only. Crop, registration, and geometry stay."""
        self.resetTransform()
        self._apply_interpolation()
        self.viewChanged.emit()

    def zoom_to(self, x: float, y: float, factor: float = 8.0) -> None:
        self.resetTransform()
        self.scale(factor, factor)
        self.centerOn(x, y)
        self.viewChanged.emit()

    def current_zoom(self) -> float:
        return float(self.transform().m11())

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if event.angleDelta().y() == 0:
            return
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        old_anchor = self.transformationAnchor()
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.scale(factor, factor)
        self.setTransformationAnchor(old_anchor)
        self._apply_interpolation()
        self.viewChanged.emit()
        event.accept()

    def _pan_by(self, delta) -> None:
        self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - delta.x()))
        self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - delta.y()))
        self.viewChanged.emit()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._right_pan and self._last_pan is not None:
            cur = event.position()
            if self._right_press is not None:
                d0 = cur - self._right_press
                if (d0.x() * d0.x() + d0.y() * d0.y()) ** 0.5 > 4:
                    if not self._right_dragged:
                        self._saved_cursor = self.cursor()
                        self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
                    self._right_dragged = True
            if self._right_dragged:
                delta = cur - self._last_pan
                self._last_pan = cur
                self._pan_by(delta)
            event.accept()
            return
        if self._middle_pan and self._last_pan is not None:
            delta = event.position() - self._last_pan
            self._last_pan = event.position()
            self._pan_by(delta)
            event.accept()
            return
        pos = self.mapToScene(event.position().toPoint())
        self._cursor = pos
        self.cursorMoved.emit(pos.x(), pos.y())
        self.previewMoved.emit(pos.x(), pos.y())
        if self._press_scene is not None and event.buttons() & Qt.MouseButton.LeftButton:
            dx = pos.x() - self._press_scene.x()
            dy = pos.y() - self._press_scene.y()
            if (dx * dx + dy * dy) ** 0.5 > 4:
                self._dragging_draw = True
                self.dragMoved.emit(self._press_scene.x(), self._press_scene.y(), pos.x(), pos.y())
        if self.show_magnifier:
            self._update_magnifier(event.position().toPoint(), pos)
        self.viewport().update()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            self._right_pan = True
            self._right_dragged = False
            self._right_press = event.position()
            self._last_pan = event.position()
            event.accept()
            return
        if event.button() == Qt.MouseButton.MiddleButton:
            self._middle_pan = True
            self._last_pan = event.position()
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and (
            self._space_pan or event.modifiers() & Qt.KeyboardModifier.AltModifier
        ):
            self._middle_pan = True
            self._last_pan = event.position()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self.dragMode() != QGraphicsView.DragMode.ScrollHandDrag:
            pos = self.mapToScene(event.position().toPoint())
            self._press_scene = pos
            self._dragging_draw = False
            self.pointClicked.emit(pos.x(), pos.y())
            return
        if event.button() == Qt.MouseButton.LeftButton and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            pos = self.mapToScene(event.position().toPoint())
            self.pointClicked.emit(pos.x(), pos.y())
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            dragged = self._right_dragged
            self._right_pan = False
            self._last_pan = None
            self._right_press = None
            if self._saved_cursor is not None:
                self.setCursor(self._saved_cursor)
                self._saved_cursor = None
            elif self.dragMode() != QGraphicsView.DragMode.ScrollHandDrag:
                self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
            else:
                self.unsetCursor()
            if dragged:
                event.accept()
                return
            # Right click without drag → context menu
            self._open_context_menu(event.globalPosition().toPoint(), self.mapToScene(event.position().toPoint()))
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._press_scene is not None:
            pos = self.mapToScene(event.position().toPoint())
            self.releasedAt.emit(pos.x(), pos.y(), bool(self._dragging_draw))
            self._press_scene = None
            self._dragging_draw = False
        if event.button() == Qt.MouseButton.MiddleButton or self._middle_pan:
            self._middle_pan = False
            self._last_pan = None
            if self.dragMode() != QGraphicsView.DragMode.ScrollHandDrag:
                self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
            else:
                self.unsetCursor()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        pos = self.mapToScene(event.position().toPoint())
        if self.dragMode() == QGraphicsView.DragMode.NoDrag:
            self.doubleClickedAt.emit(pos.x(), pos.y())
            event.accept()
            return
        if self._has_image:
            self.fit_image()
            event.accept()
            return
        item = self.scene_obj.itemAt(pos, self.transform())
        if item is not None and item.data(0):
            self.residualClicked.emit(str(item.data(0)))
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        if self._right_dragged:
            self._right_dragged = False
            event.accept()
            return
        self._open_context_menu(event.globalPos(), self.mapToScene(event.pos()))

    def _open_context_menu(self, global_pos, scene_pos) -> None:
        import os

        from app.i18n import tr

        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        menu = QMenu(self)
        if callable(getattr(self, "menuAugment", None)):
            self.menuAugment(menu, scene_pos.x(), scene_pos.y())
        for key, label_key in (
            ("replace", "ctx.replace"),
            ("paste", "ctx.paste"),
            ("remove", "ctx.remove"),
            ("fit", "ctx.fit"),
            ("actual", "ctx.actual"),
            ("reset_view", "ctx.reset_view"),
            ("zero", "ctx.zero"),
            ("scale", "ctx.scale"),
            ("control", "ctx.control"),
            ("ref", "ctx.ref"),
            ("copy", "ctx.copy"),
            ("reset_crop", "crop.reset"),
            ("edit_crop", "crop.edit"),
            ("crop", "ctx.crop"),
            ("overlay_off", "ov.off"),
        ):
            menu.addAction(tr(label_key)).setData(key)
        chosen = menu.exec(global_pos)
        if chosen is not None:
            self.contextCommand.emit(str(chosen.data()), scene_pos.x(), scene_pos.y())

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls() or event.mimeData().hasImage():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        mime = event.mimeData()
        paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()
        elif mime.hasImage():
            self.imageDropped.emit(mime.imageData())
            event.acceptProposedAction()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.magnifier.hide()
        super().leaveEvent(event)

    def set_placing(self, placing: bool) -> None:
        if placing or self.precision_drawing:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
            self.crosshair = True
            self.show_magnifier = bool(placing or self.precision_drawing)
        else:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.unsetCursor()
            self.show_magnifier = False
            self.magnifier.hide()

    def set_appearance(self, *, line=None, node=None, selected=None, handle=None, precision=None, magnifier_on=None, magnifier_original=None, marker=None, cross=None, precision_points=None, label=None) -> None:
        if line is not None:
            self.contour_line_width = float(line)
        if node is not None:
            self.contour_node_px = float(node)
        if selected is not None:
            self.contour_selected_node_px = float(selected)
        if handle is not None:
            self.contour_handle_px = float(handle)
        if precision is not None:
            self.precision_drawing = bool(precision)
            self.crosshair = True if self.precision_drawing else self.crosshair
        if precision_points is not None:
            self.precision_points = bool(precision_points)
        if marker is not None:
            self.point_marker_px = float(marker)
        if cross is not None:
            self.center_cross_px = float(cross)
        if label is not None:
            self.point_label_px = float(label)
        if magnifier_on is not None:
            self.show_magnifier = bool(magnifier_on)
            if not self.show_magnifier:
                self.magnifier.hide()
        if magnifier_original is not None:
            self.magnifier_from_original = bool(magnifier_original)

    def _update_magnifier(self, view_pt, scene_pos: QPointF) -> None:
        if not self.show_magnifier:
            self.magnifier.hide()
            return
        gp = self.mapToGlobal(view_pt)
        # Keep the lens away from the point being traced.
        ox = 72 if view_pt.x() < self.viewport().width() * 0.6 else -72 - self.magnifier.width()
        oy = 48 if view_pt.y() < self.viewport().height() * 0.6 else -48 - self.magnifier.height()
        self.magnifier.move(gp.x() + ox, gp.y() + oy)
        label = "ORIGINAL" if self.magnifier_from_original else "CURRENT VIEW"
        arr = self._original_array if self.magnifier_from_original else self._source_array
        if arr is None:
            arr = self._source_array
        if arr is not None:
            self.magnifier.update_from_array(arr, scene_pos, label=label)
        elif not self._pixmap.isNull():
            self.magnifier.update_from(self._pixmap, scene_pos)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.crop_hud.isVisible():
            self._place_crop_hud()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if not self.crosshair:
            return
        painter = QPainter(self.viewport())
        painter.setPen(QPen(QColor(255, 255, 255, 90), 1, Qt.PenStyle.DotLine))
        pt = self.mapFromScene(self._cursor)
        painter.drawLine(pt.x(), 0, pt.x(), self.height())
        painter.drawLine(0, pt.y(), self.width(), pt.y())
        if self._peer_cursor is not None:
            painter.setPen(QPen(QColor(46, 204, 113, 200), 1, Qt.PenStyle.DashLine))
            pp = self.mapFromScene(self._peer_cursor)
            painter.drawLine(pp.x(), 0, pp.x(), self.height())
            painter.drawLine(0, pp.y(), self.width(), pp.y())

    def scrollContentsBy(self, dx: int, dy: int) -> None:  # noqa: N802
        super().scrollContentsBy(dx, dy)
        self.viewChanged.emit()
