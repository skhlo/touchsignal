from __future__ import annotations

import fcntl
import os
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
from .types import DrmCard, FirmwareRow, OBSERVED_BASELINE, TouchDevice


DEFAULT_LOCK_PATH = Path("/tmp/touchsignal-touchbar-owner.lock")


class LiveHost:
    def __init__(self, lock_path: Path = DEFAULT_LOCK_PATH) -> None:
        self.lock_path = lock_path
        self._lock_fd: int | None = None
        self.opened_drm_cards: list[str] = []

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
