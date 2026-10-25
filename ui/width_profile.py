from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget


class WidthProfileWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self._canvas = None
        try:
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure

            self._fig = Figure(figsize=(5, 3), tight_layout=True)
            self._canvas = FigureCanvasQTAgg(self._fig)
            layout.addWidget(self._canvas)
        except Exception:
            from PySide6.QtWidgets import QLabel

            layout.addWidget(QLabel("Matplotlib is required to plot width profiles."))

    def plot(self, sections) -> None:
        if self._canvas is None:
            return
        self._fig.clear()
        ax1 = self._fig.add_subplot(2, 1, 1)
        ax2 = self._fig.add_subplot(2, 1, 2)
        ys = [s.position for s in sections]
        aw = [s.a_width for s in sections]
        bw = [s.b_width for s in sections]
        dw = [s.delta_width for s in sections]
        ax1.plot(ys, aw, label="A width")
        ax1.plot(ys, bw, label="B width")
        ax1.set_ylabel("width")
        ax1.legend()
        ax2.plot(ys, dw, color="tab:red", label="delta width")
        ax2.set_xlabel("position")
        ax2.set_ylabel("delta")
        ax2.legend()
        self._canvas.draw_idle()
