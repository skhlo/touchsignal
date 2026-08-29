from __future__ import annotations

import fcntl
import os
import platform
from pathlib import Path

from .attach import attach_touchbar
from .discovery import (
    competing_renderer_names,
    list_drm_cards,
    list_input_devices,
    read_firmware_row,
)
from .drm import LiveDisplaySession, inspect_appletbdrm_card, open_appletbdrm_display
from .input import LiveResourceSession, LiveTouchSession, open_backlight, open_virtual_keyboard
from .restore import restore_firmware_row
from .types import (
    OBSERVED_BASELINE,
    USB_PRODUCT,
    USB_VENDOR,
    DrmCard,
    FirmwareRow,
    TouchDevice,
)


DEFAULT_LOCK_PATH = Path("/tmp/touchsignal-touchbar-owner.lock")


class LiveHost:
    def __init__(self, lock_path: Path = DEFAULT_LOCK_PATH) -> None:
        self.lock_path = lock_path
        self._lock_fd: int | None = None
        self.opened_drm_cards: list[str] = []

    def hardware_model(self) -> str:
        path = Path("/sys/class/dmi/id/product_name")
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace").strip()

    def graphical_session_ready(self) -> bool:
        session_type = os.environ.get("XDG_SESSION_TYPE", "")
        if session_type != "wayland":
            return False
        return bool(
            os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
            or os.environ.get("WAYLAND_DISPLAY")
        )

    def missing_kernel_modules(self) -> tuple[str, ...]:
        return tuple(
            name
            for name in ("hid_appletb_kbd", "hid_appletb_bl", "appletbdrm")
            if not self._module_available(name)
        )

    def missing_permissions(self) -> tuple[str, ...]:
        missing: list[str] = []
        usb = Path("/sys/bus/usb/devices")
        config_paths = [
            path
            for path in usb.glob("*/bConfigurationValue")
            if (path.parent / "idVendor").exists()
            and (path.parent / "idProduct").exists()
            and (path.parent / "idVendor").read_text(encoding="utf-8", errors="replace").strip() == USB_VENDOR
            and (path.parent / "idProduct").read_text(encoding="utf-8", errors="replace").strip() == USB_PRODUCT
        ]
        if not config_paths or not os.access(config_paths[0], os.W_OK):
            missing.append("Touch Bar USB configuration write")
        if not os.access("/sys/class/backlight/appletb_backlight/brightness", os.W_OK):
            missing.append("Touch Bar backlight write")
        if not os.access("/dev/uinput", os.W_OK):
            missing.append("uinput write")
        if not os.access("/proc/bus/input/devices", os.R_OK):
            missing.append("input device discovery")
        return tuple(missing)

    def _module_available(self, name: str) -> bool:
        if Path(f"/sys/module/{name}").exists():
            return True
        modules = Path("/lib/modules") / platform.uname().release
        if not modules.exists():
            return False
        return any(mod.name.startswith(name + ".ko") for mod in modules.rglob(name + ".ko*"))

    def firmware_row(self) -> FirmwareRow:
        return read_firmware_row()

    def baseline_firmware_row(self) -> FirmwareRow:
        return OBSERVED_BASELINE

    def competing_renderers(self) -> tuple[str, ...]:
        return competing_renderer_names()

    def list_drm_cards(self) -> list[DrmCard]:
        cards = []
        for card in list_drm_cards():
            if card.driver == "appletbdrm":
                cards.append(inspect_appletbdrm_card(card.path))
            else:
                cards.append(card)
        return cards

    def list_touch_devices(self) -> list[TouchDevice]:
        return list_input_devices()

    def attach_display(self) -> DrmCard:
        return attach_touchbar()

    def restore_firmware_row(self) -> FirmwareRow:
        return restore_firmware_row()

    def acquire_lock(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise RuntimeError("already owned") from exc
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        os.fsync(fd)
        self._lock_fd = fd

    def release_lock(self) -> None:
        if self._lock_fd is None:
            return
        fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        os.close(self._lock_fd)
        self._lock_fd = None
        self.lock_path.unlink(missing_ok=True)

    def record_opened_drm(self, card: DrmCard) -> None:
        self.opened_drm_cards.append(card.path)

    def open_display(self, card: DrmCard) -> LiveDisplaySession:
        if card.driver != "appletbdrm":
            raise RuntimeError(f"refusing to modeset {card.driver} on {card.path}")
        self.record_opened_drm(card)
        return open_appletbdrm_display(card)

    def open_touch(self, device: TouchDevice) -> LiveTouchSession:
        if not device.is_touchbar_digitizer:
            raise RuntimeError("firmware keyboard is not a touch surface")
        return LiveTouchSession(device)

    def open_backlight(self) -> LiveResourceSession:
        return open_backlight()

    def open_virtual_input(self) -> LiveResourceSession:
        return open_virtual_keyboard()
