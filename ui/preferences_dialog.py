from __future__ import annotations

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QSpinBox,
    QVBoxLayout,
)

from app.i18n import LANG_EN, LANG_RU, LOG_EN, LOG_FOLLOW, LOG_RU, broker, tr
from app.settings import qt_settings
from models.enums import SnapMode, Unit
from ui.theme import apply_dark, apply_light


class PreferencesDialog(QDialog):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle(tr("dlg.preferences"))
        layout = QVBoxLayout(self)

        units = QGroupBox(tr("pref.units_group"))
        uform = QFormLayout(units)
        self.unit = QComboBox()
        for u, key in (
            (Unit.MM, "unit.mm"),
            (Unit.MIL, "unit.mil"),
            (Unit.INCH, "unit.inch"),
            (Unit.PX, "unit.px"),
        ):
            self.unit.addItem(tr(key), u)
        idx = next((i for i in range(self.unit.count()) if self.unit.itemData(i) == controller.data.display_unit), 0)
        self.unit.setCurrentIndex(idx)
        self.show_px = QCheckBox(tr("pref.show_pixels"))
        self.show_px.setChecked(bool(controller.data.show_pixel_values))
        self.prec = QSpinBox()
        self.prec.setRange(1, 8)
        self.prec.setValue(controller.data.display_precision)
        uform.addRow(tr("pref.display_units"), self.unit)
        uform.addRow(self.show_px)
        uform.addRow(tr("pref.display_precision"), self.prec)
        layout.addWidget(units)

        contour = QGroupBox(tr("pref.contour_group"))
        cform = QFormLayout(contour)
        self.line_w = QDoubleSpinBox()
        self.line_w.setRange(1.0, 4.0)
        self.line_w.setDecimals(1)
        self.line_w.setValue(controller.data.contour_line_width)
        self.node = QDoubleSpinBox()
        self.node.setRange(2.0, 8.0)
        self.node.setDecimals(1)
        self.node.setValue(controller.data.contour_node_px)
        self.node_sel = QDoubleSpinBox()
        self.node_sel.setRange(3.0, 10.0)
        self.node_sel.setDecimals(1)
        self.node_sel.setValue(controller.data.contour_selected_node_px)
        self.handle = QDoubleSpinBox()
        self.handle.setRange(2.0, 8.0)
        self.handle.setDecimals(1)
        self.handle.setValue(controller.data.contour_handle_px)
        self.precision = QCheckBox(tr("pref.precision_mode"))
        self.precision.setChecked(bool(controller.data.precision_drawing))
        self.prec_points = QCheckBox(tr("pref.precision_points"))
        self.prec_points.setChecked(bool(controller.data.precision_points))
        self.center_sel = QCheckBox(tr("pref.center_on_select"))
        self.center_sel.setChecked(bool(getattr(controller.data, "center_on_select", False)))
        self.marker = QDoubleSpinBox()
        self.marker.setRange(8.0, 14.0)
        self.marker.setDecimals(0)
        self.marker.setValue(controller.data.point_marker_px)
        self.cross = QDoubleSpinBox()
        self.cross.setRange(4.0, 12.0)
        self.cross.setDecimals(0)
        self.cross.setValue(controller.data.center_cross_px)
        self.label_px = QDoubleSpinBox()
        self.label_px.setRange(8.0, 18.0)
        self.label_px.setDecimals(0)
        self.label_px.setValue(controller.data.point_label_px)
        cform.addRow(tr("pref.contour_line"), self.line_w)
        cform.addRow(tr("pref.node_size"), self.node)
        cform.addRow(tr("pref.selected_node"), self.node_sel)
        cform.addRow(tr("pref.handle_size"), self.handle)
        cform.addRow(self.precision)
        cform.addRow(self.prec_points)
        cform.addRow(self.center_sel)
        cform.addRow(tr("pref.marker_size"), self.marker)
        cform.addRow(tr("pref.cross_size"), self.cross)
        cform.addRow(tr("pref.label_size"), self.label_px)
        layout.addWidget(contour)

        mag = QGroupBox(tr("pref.magnifier"))
        mform = QFormLayout(mag)
        self.mag_on = QCheckBox(tr("pref.magnifier_on"))
        self.mag_on.setChecked(bool(controller.data.magnifier_on))
        self.mag = QComboBox()
        for f in (5, 10, 20):
            self.mag.addItem(f"{f}x", f)
        self.mag.setCurrentText(f"{controller.magnifier_factor}x")
        self.mag_src = QComboBox()
        self.mag_src.addItem(tr("pref.mag_original"), True)
        self.mag_src.addItem(tr("pref.mag_current"), False)
        self.mag_src.setCurrentIndex(0 if controller.data.magnifier_from_original else 1)
        self.snap = QComboBox()
        for m in SnapMode:
            self.snap.addItem(m.value.replace("_", " "), m)
        mform.addRow(self.mag_on)
        mform.addRow(tr("pref.magnifier"), self.mag)
        mform.addRow(tr("pref.mag_source"), self.mag_src)
        mform.addRow(tr("pref.default_snap"), self.snap)
        layout.addWidget(mag)

        app = QGroupBox(tr("pref.app_group"))
        aform = QFormLayout(app)
        self.theme = QComboBox()
        self.theme.addItem(tr("pref.theme_dark"), "dark")
        self.theme.addItem(tr("pref.theme_light"), "light")
        cur_theme = str(qt_settings().value("theme", "dark"))
        self.theme.setCurrentIndex(0 if cur_theme != "light" else 1)
        self.language = QComboBox()
        self.language.addItem(tr("lang.en"), LANG_EN)
        self.language.addItem(tr("lang.ru"), LANG_RU)
        self.language.setCurrentIndex(0 if broker().language != LANG_RU else 1)
        self.log_lang = QComboBox()
        self.log_lang.addItem(tr("log.follow"), LOG_FOLLOW)
        self.log_lang.addItem(tr("lang.en"), LOG_EN)
        self.log_lang.addItem(tr("lang.ru"), LOG_RU)
        idx = {LOG_FOLLOW: 0, LOG_EN: 1, LOG_RU: 2}.get(str(qt_settings().value("log_language", LOG_FOLLOW)), 0)
        self.log_lang.setCurrentIndex(idx)
        aform.addRow(tr("pref.theme"), self.theme)
        aform.addRow(tr("pref.language"), self.language)
        aform.addRow(tr("pref.log_language"), self.log_lang)
        layout.addWidget(app)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._apply)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _apply(self) -> None:
        self.controller.data.display_unit = self.unit.currentData()
        self.controller.data.display_precision = self.prec.value()
        self.controller.data.show_pixel_values = self.show_px.isChecked()
        self.controller.data.contour_line_width = float(self.line_w.value())
        self.controller.data.contour_node_px = float(self.node.value())
        self.controller.data.contour_selected_node_px = float(self.node_sel.value())
        self.controller.data.contour_handle_px = float(self.handle.value())
        self.controller.data.precision_drawing = self.precision.isChecked()
        self.controller.data.precision_points = self.prec_points.isChecked()
        self.controller.data.center_on_select = self.center_sel.isChecked()
        self.controller.data.point_marker_px = float(self.marker.value())
        self.controller.data.center_cross_px = float(self.cross.value())
        self.controller.data.point_label_px = float(self.label_px.value())
        self.controller.data.magnifier_on = self.mag_on.isChecked()
        self.controller.data.magnifier_from_original = bool(self.mag_src.currentData())
        self.controller.magnifier_factor = self.mag.currentData()
        self.controller.snap_mode = self.snap.currentData()
        theme = self.theme.currentData()
        qt_settings().setValue("theme", theme)
        app = QApplication.instance()
        if app is not None:
            if theme == "light":
                apply_light(app)
            else:
                apply_dark(app)
        broker().set_language(self.language.currentData())
        broker().set_log_language(self.log_lang.currentData())
        self.controller.mark_dirty()
        self.accept()
