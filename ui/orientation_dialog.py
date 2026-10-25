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

from models.enums import OrientationName, TransformMode


class OrientationDialog(QDialog):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Test Orientations — nothing is applied automatically")
        self.resize(700, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Best candidate is highlighted. You must press ACCEPT ORIENTATION. "
                "BACK vs FRONT never mirrors implicitly."
            )
        )
        form = QFormLayout()
        self.mode = QComboBox()
        for m in (TransformMode.SIMILARITY, TransformMode.RIGID, TransformMode.TRANSLATION):
            self.mode.addItem(m.value, m)
        form.addRow("Fit mode", self.mode)
        layout.addLayout(form)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["ORIENTATION", "REFERENCE RMSE", "MEASUREMENT RMSE", "N POINTS", "BEST"]
        )
        layout.addWidget(self.table)
        self.pick = QComboBox()
        for name in OrientationName:
            self.pick.addItem(name.value, name)
        layout.addWidget(QLabel("Orientation to accept:"))
        layout.addWidget(self.pick)
        btns = QDialogButtonBox()
        test = btns.addButton("TEST ORIENTATIONS", QDialogButtonBox.ButtonRole.ActionRole)
        acc = btns.addButton("ACCEPT ORIENTATION", QDialogButtonBox.ButtonRole.AcceptRole)
        btns.addButton(QDialogButtonBox.StandardButton.Cancel)
        test.clicked.connect(self._test)
        acc.clicked.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        self._test()

    def _test(self) -> None:
        rows = self.controller.test_orientations(self.mode.currentData())
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [
                r.get("orientation"),
                r.get("reference_rmse"),
                r.get("measurement_rmse"),
                r.get("n_points"),
                "YES" if r.get("best") else "",
            ]
            for c, v in enumerate(vals):
                self.table.setItem(i, c, QTableWidgetItem("" if v is None else f"{v:.4f}" if isinstance(v, float) else str(v)))
            if r.get("best"):
                self.pick.setCurrentText(r["orientation"])

    def _accept(self) -> None:
        self.controller.accept_orientation(self.pick.currentData(), self.mode.currentData())
        self.accept()
