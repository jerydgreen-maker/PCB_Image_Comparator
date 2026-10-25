from __future__ import annotations

from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class GroupPanel(QWidget):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.currentTextChanged.connect(self._select)
        layout.addWidget(self.list)
        row = QHBoxLayout()
        self.new_name = QLineEdit()
        self.new_name.setPlaceholderText("New group name")
        add = QPushButton("Add group")
        add.clicked.connect(self._add)
        row.addWidget(self.new_name)
        row.addWidget(add)
        layout.addLayout(row)
        form = QFormLayout()
        self.prefix = QLineEdit()
        self.start = QSpinBox()
        self.start.setRange(1, 9999)
        self.notes = QTextEdit()
        self.notes.setMaximumHeight(80)
        self.stats = QLabel("—")
        self.stats.setWordWrap(True)
        form.addRow("Naming prefix", self.prefix)
        form.addRow("Start number", self.start)
        form.addRow("Notes", self.notes)
        self.group_tol = QDoubleSpinBox()
        self.group_tol.setDecimals(4)
        self.group_tol.setRange(0.0, 1e6)
        self.group_tol.setSpecialValueText("inherit GLOBAL")
        self.group_tol.setToolTip("0 = inherit the GLOBAL tolerance")
        form.addRow("Group tolerance", self.group_tol)
        layout.addLayout(form)
        apply_btn = QPushButton("Apply naming / notes")
        apply_btn.clicked.connect(self._apply)
        layout.addWidget(apply_btn)
        layout.addWidget(QLabel("Internal geometry vs board position"))
        layout.addWidget(self.stats)
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        for g in self.controller.data.groups:
            self.list.addItem(g.name)
        self._render_stats()

    def _select(self, name: str) -> None:
        if not name:
            return
        self.controller.active_group = name
        g = next((x for x in self.controller.data.groups if x.name == name), None)
        if g:
            self.prefix.setText(g.naming_prefix or name[:3])
            self.start.setValue(g.naming_start)
            self.notes.setPlainText(g.notes)
            self.group_tol.setValue(self.controller.data.tolerances.group_match.get(name, 0.0))
            self.controller.naming_prefix = g.naming_prefix or name[:3]
            self.controller.naming_start = g.naming_start
        self._render_stats()

    def _add(self) -> None:
        name = self.new_name.text().strip()
        if name:
            self.controller.add_group(name)
            self.new_name.clear()
            self.refresh()

    def _apply(self) -> None:
        name = self.list.currentItem().text() if self.list.currentItem() else ""
        g = next((x for x in self.controller.data.groups if x.name == name), None)
        if not g:
            return
        g.naming_prefix = self.prefix.text().strip()
        g.naming_start = self.start.value()
        g.notes = self.notes.toPlainText()
        self.controller.set_group_tolerance(name, None if self.group_tol.value() <= 0 else self.group_tol.value())
        self.controller.naming_prefix = g.naming_prefix
        self.controller.naming_start = g.naming_start
        self.controller.mark_dirty()

    def _render_stats(self) -> None:
        comps = self.controller.group_comparisons()
        lines = []
        for c in comps:
            ga = c.geometry_a
            gb = c.geometry_b
            line = f"<b>{c.name}</b> internal: {c.internal_state.value}"
            if ga and ga.mean_pitch is not None:
                line += f"<br/>Mean pitch A={ga.mean_pitch:.4f}"
            if gb and gb.mean_pitch is not None:
                line += f"  B={gb.mean_pitch:.4f}"
            if c.board_dx is not None:
                line += f"<br/>Board position DX={c.board_dx:.4f} DY={c.board_dy:.4f}"
            if c.flags:
                line += f"<br/>Flags: {', '.join(f.value for f in c.flags)} (diagnostic)"
            lines.append(line)
        self.stats.setText("<br/><br/>".join(lines) or "No group points yet.")
