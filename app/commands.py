"""Project-level QUndoCommand types. One stack. One history."""

from __future__ import annotations

import copy

from PySide6.QtGui import QUndoCommand

from models.document import ProjectData
from models.enums import Side


def _capture(project: ProjectData, keys) -> dict:
    out = {}
    for key in keys:
        out[key] = copy.deepcopy(getattr(project, key))
    return out


def _restore(project: ProjectData, snap: dict) -> None:
    for key, value in snap.items():
        setattr(project, key, copy.deepcopy(value))


class RestoreCommand(QUndoCommand):
    """State was already changed. First redo is a no-op; undo restores `before`."""

    def __init__(self, project: ProjectData, text: str, before: dict):
        super().__init__(text)
        self._project = project
        self._before = before
        self._after = _capture(project, before.keys())
        self._virgin = True

    def redo(self) -> None:
        if self._virgin:
            self._virgin = False
            return
        _restore(self._project, self._after)

    def undo(self) -> None:
        _restore(self._project, self._before)


class SnapshotCommand(QUndoCommand):
    def __init__(self, project: ProjectData, text: str, apply_fn, revert_snapshot: dict):
        super().__init__(text)
        self._project = project
        self._apply = apply_fn
        self._before = revert_snapshot
        self._after = None

    def redo(self) -> None:
        if self._after is None:
            self._apply()
            self._after = _capture(self._project, self._before.keys())
        else:
            _restore(self._project, self._after)

    def undo(self) -> None:
        _restore(self._project, self._before)


class MovePointCommand(QUndoCommand):
    def __init__(self, project: ProjectData, landmark, side: Side, new_xy: tuple[float, float], old_xy: tuple[float, float] | None):
        super().__init__(f"Move {landmark.label or landmark.id}")
        self._project = project
        self.landmark_id = landmark.id
        self.side = side
        self.new_xy = new_xy
        self.old_xy = old_xy

    def _lm(self):
        return next((x for x in self._project.landmarks if x.id == self.landmark_id), None)

    def redo(self) -> None:
        lm = self._lm()
        if lm is not None:
            lm.set_coord(self.side, *self.new_xy)

    def undo(self) -> None:
        lm = self._lm()
        if lm is None:
            return
        if self.old_xy is None:
            if self.side == Side.A:
                lm.a_x = lm.a_y = None
            else:
                lm.b_x = lm.b_y = None
        else:
            lm.set_coord(self.side, *self.old_xy)


class MergeableAdjustCommand(QUndoCommand):
    """Slider compression: one Ctrl+Z restores the value before the drag."""

    MERGE_BASE = 91001

    def __init__(self, project: ProjectData, side: Side, field: str, old, new, text: str, token: int):
        super().__init__(text)
        self._project = project
        self.side = side
        self.field = field
        self.old = old
        self.new = new
        self.token = token

    def _adj(self):
        doc = self._project.image_a if self.side == Side.A else self._project.image_b
        return None if doc is None else doc.adjustments

    def id(self) -> int:
        return self.MERGE_BASE + (self.token % 1000)

    def mergeWith(self, other: QUndoCommand) -> bool:  # noqa: N802
        if not isinstance(other, MergeableAdjustCommand):
            return False
        if other.field != self.field or other.side != self.side:
            return False
        self.new = other.new
        return True

    def redo(self) -> None:
        adj = self._adj()
        if adj is not None:
            setattr(adj, self.field, self.new)

    def undo(self) -> None:
        adj = self._adj()
        if adj is not None:
            setattr(adj, self.field, self.old)
