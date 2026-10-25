from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFileDialog, QLabel, QVBoxLayout


class ReportDialog(QDialog):
    def __init__(self, controller, parent=None, extra_images: dict | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.extra_images = extra_images or {}
        self.setWindowTitle("PDF Verification Report")
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "The report separates MEASURED, DERIVED and UNVERIFIED. "
                "Inferred or cropped features are never presented as measured."
            )
        )
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save PDF report", "report.pdf", "PDF (*.pdf)")
        if not path:
            return
        from exporters.pdf_report import generate_pdf

        generate_pdf(self.controller.data, path, extra_images=self.extra_images)
        self.accept()
