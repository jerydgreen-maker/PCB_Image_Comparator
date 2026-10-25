"""Non-destructive display adjustments. Never written into source pixels."""

from __future__ import annotations

from dataclasses import dataclass


PRESETS = {
    "ORIGINAL": dict(
        brightness=0,
        contrast=0,
        sharpness=0,
        gamma=1.0,
        saturation=0,
        exposure=0,
        grayscale=False,
        invert=False,
        clahe=False,
        edge_enhance=False,
        clarity=0,
        denoise=0,
        highlights=0,
        shadows=0,
        enhance_mode="OFF",
    ),
    "PCB COPPER": dict(brightness=6, contrast=18, sharpness=20, gamma=0.92, saturation=10, enhance_mode="MANUAL"),
    "PCB EDGE": dict(brightness=0, contrast=28, sharpness=35, gamma=0.85, edge_enhance=True, clarity=22, enhance_mode="MANUAL"),
    "DARK DETAILS": dict(brightness=12, contrast=10, sharpness=8, gamma=0.75, shadows=20, enhance_mode="MANUAL"),
    "HIGH CONTRAST": dict(brightness=0, contrast=40, sharpness=10, gamma=1.0, enhance_mode="MANUAL"),
}


@dataclass
class ImageAdjustments:
    brightness: float = 0.0  # -100 .. 100
    contrast: float = 0.0
    sharpness: float = 0.0
    gamma: float = 1.0
    saturation: float = 0.0
    exposure: float = 0.0
    grayscale: bool = False
    invert: bool = False
    clahe: bool = False
    edge_enhance: bool = False
    clarity: float = 0.0
    denoise: float = 0.0
    highlights: float = 0.0
    shadows: float = 0.0
    # OFF | MANUAL | AUTO | ADVANCED
    enhance_mode: str = "OFF"
    # original | enhanced | split | flicker
    compare_mode: str = "enhanced"
    # Super Resolution factor. 1 = unused. Never treated as metrology.
    super_res_factor: int = 1

    def is_neutral(self) -> bool:
        return (
            self.brightness == 0
            and self.contrast == 0
            and self.sharpness == 0
            and abs(self.gamma - 1.0) < 1e-9
            and self.saturation == 0
            and self.exposure == 0
            and not self.grayscale
            and not self.invert
            and not self.clahe
            and not self.edge_enhance
            and self.clarity == 0
            and self.denoise == 0
            and self.highlights == 0
            and self.shadows == 0
            and int(self.super_res_factor) <= 1
        )

    def display_is_enhanced(self) -> bool:
        return (not self.is_neutral()) and self.enhance_mode != "OFF"

    def apply_preset(self, name: str) -> None:
        data = PRESETS.get(name)
        if not data:
            return
        for k, v in PRESETS["ORIGINAL"].items():
            setattr(self, k, v)
        for k, v in data.items():
            setattr(self, k, v)

    def as_dict(self) -> dict:
        return {
            "brightness": self.brightness,
            "contrast": self.contrast,
            "sharpness": self.sharpness,
            "gamma": self.gamma,
            "saturation": self.saturation,
            "exposure": self.exposure,
            "grayscale": self.grayscale,
            "invert": self.invert,
            "clahe": self.clahe,
            "edge_enhance": self.edge_enhance,
            "clarity": self.clarity,
            "denoise": self.denoise,
            "highlights": self.highlights,
            "shadows": self.shadows,
            "enhance_mode": self.enhance_mode,
            "compare_mode": self.compare_mode,
            "super_res_factor": int(self.super_res_factor),
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "ImageAdjustments":
        if not data:
            return cls()
        return cls(
            brightness=float(data.get("brightness", 0)),
            contrast=float(data.get("contrast", 0)),
            sharpness=float(data.get("sharpness", 0)),
            gamma=float(data.get("gamma", 1.0)),
            saturation=float(data.get("saturation", 0)),
            exposure=float(data.get("exposure", 0)),
            grayscale=bool(data.get("grayscale", False)),
            invert=bool(data.get("invert", False)),
            clahe=bool(data.get("clahe", False)),
            edge_enhance=bool(data.get("edge_enhance", False)),
            clarity=float(data.get("clarity", 0)),
            denoise=float(data.get("denoise", 0)),
            highlights=float(data.get("highlights", 0)),
            shadows=float(data.get("shadows", 0)),
            enhance_mode=str(data.get("enhance_mode", "OFF") or "OFF"),
            compare_mode=str(data.get("compare_mode", "enhanced") or "enhanced"),
            super_res_factor=int(data.get("super_res_factor", 1) or 1),
        )
