from __future__ import annotations

from dataclasses import dataclass, field

NATIVE_WIDTH = 2008
NATIVE_HEIGHT = 60
USB_VENDOR = "05ac"
USB_PRODUCT = "8302"
FIRMWARE_CONFIG = "1"
DRM_CONFIG = "2"
TOUCH_MAX_X = 32767
TOUCH_MAX_Y = 127
SUPPORTED_HARDWARE_MODEL = "MacBookPro16,1"

COMPETING_RENDERERS = (
    "tiny-dfr",
    "react-drm",
    "linux-touchbar-control-center",
    "mac-touchbar-plus",
)


@dataclass(frozen=True)
class FirmwareRow:
    usb_configuration: str
    special_key_mode: str
    fn_toggle: str
    autodim: str
    brightness: str
    appletbdrm_loaded: bool


OBSERVED_BASELINE = FirmwareRow(
    usb_configuration=FIRMWARE_CONFIG,
    special_key_mode="2",
    fn_toggle="Y",
    autodim="Y",
    brightness="2",
    appletbdrm_loaded=False,
)


def firmware_row_restored(row: FirmwareRow, baseline: FirmwareRow = OBSERVED_BASELINE) -> bool:
    if row.usb_configuration != baseline.usb_configuration:
        return False
    if row.special_key_mode != baseline.special_key_mode:
        return False
    if row.fn_toggle != baseline.fn_toggle:
        return False
    if row.autodim != baseline.autodim:
        return False
    if row.appletbdrm_loaded:
        return False
    if row.brightness == baseline.brightness:
        return True
    return baseline.autodim == "Y" and row.brightness in {"0", "1", "2"}


@dataclass(frozen=True)
class DrmCard:
    name: str
    driver: str
    path: str
    hdisplay: int
    vdisplay: int
    rotate90: bool

    @property
    def logical_size(self) -> tuple[int, int]:
        if self.rotate90:
            return (self.vdisplay, self.hdisplay)
        return (self.hdisplay, self.vdisplay)


@dataclass(frozen=True)
class TouchDevice:
    name: str
    path: str

    @property
    def is_firmware_keyboard(self) -> bool:
        return self.name == "Apple Inc. Touch Bar Display" and "Touchpad" not in self.name

    @property
    def is_touchbar_digitizer(self) -> bool:
        return "Touch Bar" in self.name and not self.is_firmware_keyboard


@dataclass(frozen=True)
class TouchEvent:
    kind: str
    x: int
    y: int


@dataclass
class OwnerState:
    claimed: set[str] = field(default_factory=set)
    touch_events: list[TouchEvent] = field(default_factory=list)


@dataclass(frozen=True)
class RuntimeFrame:
    surface_size: tuple[int, int]
    touch_count: int
    last_touch: TouchEvent | None


@dataclass(frozen=True)
class RuntimeAction:
    kind: str
    touch: TouchEvent


@dataclass
class RuntimeState:
    frames: list[RuntimeFrame] = field(default_factory=list)
    touch_events: list[TouchEvent] = field(default_factory=list)
    actions: list[RuntimeAction] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    restarts: int = 0
    running: bool = False
