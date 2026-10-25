from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from models.enums import TransformMode


class TransformDialog(QDialog):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Review Transform — values are not applied until ACCEPT")
        self.resize(640, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("Nothing is silently applied. Review numbers, then Accept or Cancel.")
        )
        form = QFormLayout()
        self.mode = QComboBox()
        for m in TransformMode:
            label = m.value
            if m == TransformMode.AFFINE:
                label += " (DIAGNOSTIC ONLY)"
            self.mode.addItem(label, m)
        form.addRow("Mode", self.mode)
        layout.addLayout(form)
        self.summary = QLabel("—")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["ID", "DX", "DY", "ERROR", "OUTLIER?"])
        layout.addWidget(self.table)
        self.affine = QLabel("")
        self.affine.setWordWrap(True)
        layout.addWidget(self.affine)
        btns = QDialogButtonBox()
        self.compute_btn = btns.addButton("Compute provisional", QDialogButtonBox.ButtonRole.ActionRole)
        self.accept_btn = btns.addButton("ACCEPT TRANSFORM", QDialogButtonBox.ButtonRole.AcceptRole)
        btns.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.compute_btn.clicked.connect(self._compute)
        self.accept_btn.clicked.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        self._compute()

    def _compute(self) -> None:
        rec = self.controller.compute_provisional(self.mode.currentData())
        if rec is None:
            self.summary.setText("Could not compute transform.")
            return
        t = rec.transform
        s = rec.summary
        self.summary.setText(
            f"<b>PROVISIONAL</b>  points={s.get('count')}  scale={t.scale:.6f}  "
            f"rotation={t.rotation_deg:.4f}°  TX={t.tx:.4f}  TY={t.ty:.4f}<br/>"
            f"mean={s.get('mean')}  median={s.get('median')}  RMSE={s.get('rmse')}  max={s.get('max')}<br/>"
            + "<br/>".join(rec.notes)
        )
        outliers = self.controller.outlier_ids()
        self.table.setRowCount(len(rec.residuals))
        for i, r in enumerate(rec.residuals):
            for c, key in enumerate(("point_id", "dx", "dy", "distance")):
                self.table.setItem(i, c, QTableWidgetItem(str(r.get(key, ""))))
            self.table.setItem(i, 4, QTableWidgetItem("YES" if r.get("point_id") in outliers else ""))
        if rec.affine:
            self.affine.setText(
                "AFFINE DIAGNOSTIC ONLY — not physical proof. "
                f"scaleX={rec.affine.get('scale_x'):.6f} scaleY={rec.affine.get('scale_y'):.6f} "
                f"shear={rec.affine.get('shear'):.6f} rot={rec.affine.get('rotation_deg'):.4f}° "
                f"TX={rec.affine.get('tx'):.4f} TY={rec.affine.get('ty'):.4f}"
            )
        else:
            self.affine.setText("")

    def _accept(self) -> None:
        self.controller.accept_transform()
        self.accept()
