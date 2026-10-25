from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget


class Magnifier(QWidget):
    """Floating magnifier. Offset from the cursor so it never covers the traced point."""

    def __init__(self, parent=None, size: int = 168) -> None:
        super().__init__(parent, Qt.WindowType.Tool)
        self.setFixedSize(size, size)
        self._pixmap = QPixmap()
        self._factor = 10
        self._source_label = "ORIGINAL"
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

    def set_factor(self, factor: int) -> None:
        if factor not in (5, 10, 20, 40):
            return
        self._factor = factor

    def update_from_array(self, array, scene_pos: QPointF, *, label: str = "ORIGINAL") -> None:
        """Nearest-neighbour crop from full-resolution pixels (original or current view)."""
        if array is None:
            self.hide()
            return
        import numpy as np

        self._source_label = label
        src = max(4, self.width() // self._factor)
        cx, cy = int(round(scene_pos.x())), int(round(scene_pos.y()))
        h, w = array.shape[:2]
        x0 = max(0, min(w - src, cx - src // 2))
        y0 = max(0, min(h - src, cy - src // 2))
        crop = np.ascontiguousarray(array[y0 : y0 + src, x0 : x0 + src])
        if crop.size == 0:
            self.hide()
            return
        if crop.ndim == 2:
            rgb = np.stack([crop, crop, crop], axis=-1)
        else:
            rgb = crop[:, :, ::-1].copy()
        qimg = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888).copy()
        self._pixmap = QPixmap.fromImage(
            qimg.scaled(
                self.size(),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
        )
        self.update()
        self.show()

    def update_from(self, pixmap: QPixmap, scene_pos: QPointF) -> None:
        if pixmap.isNull():
            self.hide()
            return
        src = max(4, self.width() // self._factor)
        cx, cy = scene_pos.x(), scene_pos.y()
        rect = QRect(int(cx - src / 2), int(cy - src / 2), src, src)
        cropped = pixmap.copy(rect)
        self._pixmap = cropped.scaled(
            self.size(),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self._source_label = "CURRENT VIEW"
        self.update()
        self.show()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(20, 20, 20))
        if not self._pixmap.isNull():
            painter.drawPixmap(0, 0, self._pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cx, cy = self.width() // 2, self.height() // 2
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 80, 80), 1.5))
        painter.drawEllipse(QPointF(cx, cy), 12.0, 12.0)
        painter.setPen(QPen(QColor(255, 80, 80), 1))
        painter.drawLine(cx, 0, cx, self.height())
        painter.drawLine(0, cy, self.width(), cy)
        painter.setPen(QPen(QColor(240, 240, 240), 1))
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)
        painter.drawText(4, 14, f"{self._factor}x  {self._source_label}")
