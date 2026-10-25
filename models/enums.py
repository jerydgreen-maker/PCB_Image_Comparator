"""Shared enumerations for PCB Image Comparator.

These values appear in the UI, project files, CSV headers, and reports.
Keep names stable — they are part of the .picmp contract.
"""

from __future__ import annotations

from enum import Enum


class Side(str, Enum):
    A = "A"
    B = "B"


class PointType(str, Enum):
    ZERO = "ZERO"
    SCALE = "SCALE"
    CONTROL = "CONTROL"
    REFERENCE = "REFERENCE"  # Advanced Mode multi-point registration
    MEASUREMENT = "MEASUREMENT"  # alias of CONTROL in Advanced Mode
    CALIBRATION = "CALIBRATION"
    CONTOUR = "CONTOUR"


class TransformMode(str, Enum):
    TRANSLATION = "TRANSLATION"
    RIGID = "RIGID"
    SIMILARITY = "SIMILARITY"
    AFFINE = "AFFINE"  # DIAGNOSTIC ONLY


class TransformStatus(str, Enum):
    NONE = "NONE"
    PROVISIONAL = "PROVISIONAL"
    ACCEPTED = "ACCEPTED"
    LOCKED = "LOCKED"


class OrientationName(str, Enum):
    NORMAL = "NORMAL"
    MIRROR_X = "MIRROR_X"
    MIRROR_Y = "MIRROR_Y"
    ROTATE_90 = "ROTATE_90"
    ROTATE_180 = "ROTATE_180"
    ROTATE_270 = "ROTATE_270"
    MIRROR_X_ROTATE_90 = "MIRROR_X_ROTATE_90"
    MIRROR_X_ROTATE_180 = "MIRROR_X_ROTATE_180"
    MIRROR_X_ROTATE_270 = "MIRROR_X_ROTATE_270"
    MIRROR_Y_ROTATE_90 = "MIRROR_Y_ROTATE_90"
    MIRROR_XY = "MIRROR_XY"


class Confidence(str, Enum):
    CONFIRMED = "CONFIRMED"
    CONFIRMED_VISIBLE = "CONFIRMED_VISIBLE"
    PARTIAL = "PARTIAL"
    PARTIALLY_VISIBLE = "PARTIALLY_VISIBLE"
    CROPPED = "CROPPED"
    UNVERIFIED = "UNVERIFIED"
    INFERRED = "INFERRED"


class ResultState(str, Enum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    UNVERIFIED = "UNVERIFIED"
    WARNING = "WARNING"
    UNKNOWN = "UNKNOWN"


class DiagnosticFlag(str, Enum):
    POSITION_MISMATCH = "POSITION_MISMATCH"
    SIZE_MISMATCH = "SIZE_MISMATCH"
    PITCH_MISMATCH = "PITCH_MISMATCH"
    ROTATION_MISMATCH = "ROTATION_MISMATCH"
    SCALE_SUSPECT = "SCALE_SUSPECT"
    MIRROR_SUSPECT = "MIRROR_SUSPECT"
    REGISTRATION_SUSPECT = "REGISTRATION_SUSPECT"
    POSSIBLE_GLOBAL_REGISTRATION_ERROR = "POSSIBLE_GLOBAL_REGISTRATION_ERROR"
    POSSIBLE_SCALE_ERROR = "POSSIBLE_SCALE_ERROR"
    POSSIBLE_ROTATION_ERROR = "POSSIBLE_ROTATION_ERROR"
    POSSIBLE_MIRROR_ORIENTATION_ERROR = "POSSIBLE_MIRROR_ORIENTATION_ERROR"


class OverlayMode(str, Enum):
    A_ONLY = "A_ONLY"
    B_ONLY = "B_ONLY"
    OVERLAY = "OVERLAY"  # legacy alias of B_OVER_A
    B_OVER_A = "B_OVER_A"
    A_OVER_B = "A_OVER_B"
    DIFFERENCE = "DIFFERENCE"
    EDGE_DIFFERENCE = "EDGE_DIFFERENCE"
    FLICKER = "FLICKER"


def overlay_is_registered(mode: OverlayMode) -> bool:
    return mode in (OverlayMode.OVERLAY, OverlayMode.B_OVER_A, OverlayMode.A_OVER_B)


def overlay_base_side(mode: OverlayMode):
    """Which image stays untransformed as the overlay workspace base."""
    if mode == OverlayMode.A_OVER_B:
        return Side.B
    return Side.A


class ActiveTool(str, Enum):
    PAN = "PAN"
    REFERENCE = "REFERENCE"
    MEASUREMENT = "MEASUREMENT"
    CALIBRATION = "CALIBRATION"
    DISTANCE = "DISTANCE"
    ANGLE = "ANGLE"
    CIRCLE = "CIRCLE"
    PITCH = "PITCH"
    CONTOUR = "CONTOUR"
    DIMENSION = "DIMENSION"
    CROP = "CROP"
    CROSS_SECTION = "CROSS_SECTION"
    ZERO = "ZERO"
    SCALE = "SCALE"
    CONTROL = "CONTROL"
    SNAP_CONFIRM = "SNAP_CONFIRM"
    SELECT = "SELECT"


class MeshViewMode(str, Enum):
    HIDDEN = "HIDDEN"
    POINTS_ONLY = "POINTS_ONLY"
    MESH = "MESH"
    MESH_VECTORS = "MESH_VECTORS"
    EXPECTED = "EXPECTED"
    ACTUAL = "ACTUAL"
    BOTH = "BOTH"
    GHOST = "GHOST"
    ERROR_MAP = "ERROR_MAP"
    LOCAL_SCALE = "LOCAL_SCALE"
    LOCAL_ROTATION = "LOCAL_ROTATION"
    STRETCH = "STRETCH"
    SKELETON = "SKELETON"


class SnapMode(str, Enum):
    NONE = "NONE"
    EDGE = "EDGE"
    CIRCLE_CENTER = "CIRCLE_CENTER"
    PAD_CENTER = "PAD_CENTER"
    LINE_INTERSECTION = "LINE_INTERSECTION"


class Unit(str, Enum):
    PX = "px"
    MM = "mm"
    CM = "cm"
    INCH = "inch"
    MIL = "mil"


class PcbSide(str, Enum):
    FRONT = "FRONT"
    BACK = "BACK"
    UNKNOWN = "UNKNOWN"


class AxisX(str, Enum):
    RIGHT = "RIGHT"
    LEFT = "LEFT"


class AxisY(str, Enum):
    DOWN = "DOWN"
    UP = "UP"


class OriginMode(str, Enum):
    CUSTOM = "CUSTOM"
    TOP_LEFT = "TOP_LEFT"
    BOTTOM_LEFT = "BOTTOM_LEFT"
    CENTROID = "CENTROID"
    REFERENCE = "REFERENCE"


class ContourKind(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class LayerName(str, Enum):
    BOARD_OUTLINE = "BOARD_OUTLINE"
    PADS = "PADS"
    VIAS = "VIAS"
    TRACKS = "TRACKS"
    COPPER = "COPPER"
    HOLES = "HOLES"
    SLOTS = "SLOTS"
    SILKSCREEN = "SILKSCREEN"
    COMPONENTS = "COMPONENTS"
    OTHER = "OTHER"


class FactClass(str, Enum):
    MEASURED = "MEASURED"
    DERIVED = "DERIVED"
    UNVERIFIED = "UNVERIFIED"


class CrossSectionAxis(str, Enum):
    HORIZONTAL = "HORIZONTAL"
    VERTICAL = "VERTICAL"


PCB_PRESET_GROUPS = (
    "BOARD_OUTLINE",
    "KEYPAD",
    "LCD",
    "BATTERY",
    "SIM",
    "CHARGER",
    "CONNECTOR",
    "BOTTOM_CONNECTOR",
    "EARPIECE",
    "MICROPHONE",
    "MOUNTING",
    "OUTLINE",
    "CUSTOM",
)

DEFAULT_CONFIDENCE_FOR_STATS = (Confidence.CONFIRMED, Confidence.CONFIRMED_VISIBLE)
