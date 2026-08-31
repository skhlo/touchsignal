from __future__ import annotations

import fcntl
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .types import (
    DRM_CONFIG,
    FIRMWARE_CONFIG,
    NATIVE_HEIGHT,
    NATIVE_WIDTH,
    OBSERVED_BASELINE,
    DrmCard,
    FnEvent,
    FirmwareRow,
    KEY_FN,
    MediaAction,
    RuntimeFrame,
    InputDevice,
    TouchEvent,
)


class Host(Protocol):
    def hardware_model(self) -> str: ...
    def graphical_session_ready(self) -> bool: ...
    def missing_kernel_modules(self) -> tuple[str, ...]: ...
    def missing_permissions(self) -> tuple[str, ...]: ...
    def firmware_row(self) -> FirmwareRow: ...
    def baseline_firmware_row(self) -> FirmwareRow: ...
    def competing_renderers(self) -> tuple[str, ...]: ...
    def list_drm_cards(self) -> list[DrmCard]: ...
    def list_input_devices(self) -> list[InputDevice]: ...
    def attach_display(self) -> DrmCard: ...
    def restore_firmware_row(self) -> FirmwareRow: ...
    def acquire_lock(self) -> None: ...
    def release_lock(self) -> None: ...
    def open_display(self, card: DrmCard) -> DisplaySession: ...
    def open_touch(self, device: InputDevice) -> TouchSession: ...
    def open_fn_input(self, device: InputDevice) -> FnSession: ...
    def wait_for_input(
        self,
        fn_session: FnSession,
        touch_session: TouchSession,
        timeout: float,
    ) -> None: ...
    def open_backlight(self) -> ResourceSession: ...
    def open_virtual_keyboard(self) -> VirtualKeyboardSession: ...


class DisplaySession(Protocol):
    @property
    def size(self) -> tuple[int, int]: ...
    def present_test_surface(self) -> None: ...
    def present_frame(self, frame: RuntimeFrame) -> None: ...
    def close(self) -> None: ...


class TouchSession(Protocol):
    def read_events(self, timeout: float = 0.0) -> list[TouchEvent]: ...
    def close(self) -> None: ...


class FnSession(Protocol):
    def read_events(self, timeout: float = 0.0) -> list[FnEvent]: ...

    def close(self) -> None: ...


class VirtualKeyboardSession(Protocol):
    def emit(self, action: MediaAction) -> None: ...

    def close(self) -> None: ...


class ResourceSession(Protocol):
    def close(self) -> None: ...


class FakeDisplaySession:
    def __init__(
        self,
        card: DrmCard,
        presented: list[tuple[int, int]],
        presented_frames: list[RuntimeFrame],
        closed_sessions: list[str],
        name: str = "display",
    ) -> None:
        self._card = card
        self._presented = presented
        self._presented_frames = presented_frames
        self._closed_sessions = closed_sessions
        self._name = name
        self.closed = False

    @property
    def size(self) -> tuple[int, int]:
        return self._card.logical_size

    def present_test_surface(self) -> None:
        if self.closed:
            raise RuntimeError("display already closed")
        self._presented.append(self.size)

    def present_frame(self, frame: RuntimeFrame) -> None:
        if self.closed:
            raise RuntimeError("display already closed")
        if frame.surface_size != self.size:
            raise RuntimeError(f"frame is {frame.surface_size}, expected {self.size}")
        self._presented_frames.append(frame)

    def close(self) -> None:
        if not self.closed:
            self._closed_sessions.append(self._name)
        self.closed = True


class FakeTouchSession:
    def __init__(
        self,
        queued: list[TouchEvent],
        closed_sessions: list[str],
        read_timeouts: list[float],
    ) -> None:
        self._queued = list(queued)
        self._closed_sessions = closed_sessions
        self._read_timeouts = read_timeouts
        self.closed = False

    def read_events(self, timeout: float = 0.0) -> list[TouchEvent]:
        if self.closed:
            raise RuntimeError("touch already closed")
        self._read_timeouts.append(timeout)
        events = list(self._queued)
        self._queued.clear()
        return events

    def close(self) -> None:
        if not self.closed:
            self._closed_sessions.append("touch")
        self.closed = True


class FakeResourceSession:
    def __init__(self, closed_sessions: list[str], name: str) -> None:
        self._closed_sessions = closed_sessions
        self._name = name
        self.closed = False

    def close(self) -> None:
        if not self.closed:
            self._closed_sessions.append(self._name)
        self.closed = True


class FakeFnSession:
    def __init__(self, queued: list[FnEvent], closed_sessions: list[str]) -> None:
        self._queued = queued
        self._closed_sessions = closed_sessions
        self.closed = False

    def read_events(self, timeout: float = 0.0) -> list[FnEvent]:
        if self.closed:
            raise RuntimeError("Fn input already closed")
        events = list(self._queued)
        self._queued.clear()
        return events

    def close(self) -> None:
        if not self.closed:
            self._closed_sessions.append("fn_input")
        self.closed = True


class FakeVirtualKeyboardSession:
    def __init__(
        self,
        emitted: list[MediaAction],
        closed_sessions: list[str],
    ) -> None:
        self._emitted = emitted
        self._closed_sessions = closed_sessions
        self.closed = False

    def emit(self, action: MediaAction) -> None:
        if self.closed:
            raise RuntimeError("virtual keyboard already closed")
        self._emitted.append(action)

    def close(self) -> None:
        if not self.closed:
            self._closed_sessions.append("virtual_keyboard")
        self.closed = True


@dataclass
class FakeHost:
    root: Path
    model: str = "MacBookPro16,1"
    graphical_session: bool = True
    missing_modules: tuple[str, ...] = ()
    permission_errors: tuple[str, ...] = ()
    competing: tuple[str, ...] = ()
    appletbdrm_appears: bool = True
    attach_succeeds: bool = True
    usb_configuration: str = FIRMWARE_CONFIG
    special_key_mode: str = OBSERVED_BASELINE.special_key_mode
    fn_toggle: str = OBSERVED_BASELINE.fn_toggle
    autodim: str = OBSERVED_BASELINE.autodim
    brightness: str = OBSERVED_BASELINE.brightness
    appletbdrm_loaded: bool = False
    opened_drm_cards: list[str] = field(default_factory=list)
    presented_surfaces: list[tuple[int, int]] = field(default_factory=list)
    presented_frames: list[RuntimeFrame] = field(default_factory=list)
    queued_touch_events: list[TouchEvent] = field(default_factory=list)
    touch_read_timeouts: list[float] = field(default_factory=list)
    queued_fn_events: list[FnEvent] = field(default_factory=list)
    input_wait_timeouts: list[float] = field(default_factory=list)
    emitted_media_actions: list[MediaAction] = field(default_factory=list)
    closed_sessions: list[str] = field(default_factory=list)
    operations: list[str] = field(default_factory=list)
    _lock_fd: int | None = None
    _display: FakeDisplaySession | None = None
    _touch: FakeTouchSession | None = None
    _fn_input: FakeFnSession | None = None
    _backlight: FakeResourceSession | None = None
    _virtual_keyboard: FakeVirtualKeyboardSession | None = None

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.root / "touchbar-owner.lock"

    def hardware_model(self) -> str:
        return self.model

    def graphical_session_ready(self) -> bool:
        return self.graphical_session

    def missing_kernel_modules(self) -> tuple[str, ...]:
        return self.missing_modules

    def missing_permissions(self) -> tuple[str, ...]:
        return self.permission_errors

    def baseline_firmware_row(self) -> FirmwareRow:
        return OBSERVED_BASELINE

    def firmware_row(self) -> FirmwareRow:
        return FirmwareRow(
            usb_configuration=self.usb_configuration,
            special_key_mode=self.special_key_mode,
            fn_toggle=self.fn_toggle,
            autodim=self.autodim,
            brightness=self.brightness,
            appletbdrm_loaded=self.appletbdrm_loaded,
        )

    def competing_renderers(self) -> tuple[str, ...]:
        return self.competing

    def list_drm_cards(self) -> list[DrmCard]:
        cards = [
            DrmCard("card1", "i915", "/dev/dri/card1", 3072, 1920, False),
            DrmCard("card2", "amdgpu", "/dev/dri/card2", 3072, 1920, False),
        ]
        if self.appletbdrm_loaded and self.appletbdrm_appears:
            cards.append(
                DrmCard(
                    "card3",
                    "appletbdrm",
                    "/dev/dri/card3",
                    NATIVE_HEIGHT,
                    NATIVE_WIDTH,
                    True,
                )
            )
        return cards

    def list_input_devices(self) -> list[InputDevice]:
        devices = [
            InputDevice("Apple Inc. Touch Bar Display", "/dev/input/event7"),
            InputDevice(
                "Apple Inc. Apple Internal Keyboard / Trackpad",
                "/dev/input/event5",
                bus="0003",
                vendor="05ac",
                product="0340",
                key_codes=frozenset({KEY_FN}),
            ),
        ]
        if self.usb_configuration == DRM_CONFIG:
            devices.append(
                InputDevice(
                    "Apple Inc. Touch Bar Display Touchpad",
                    "/dev/input/event20",
                )
            )
        return devices

    def attach_display(self) -> DrmCard:
        self.operations.append("attach")
        if not self.attach_succeeds:
            self.usb_configuration = FIRMWARE_CONFIG
            self.appletbdrm_loaded = False
            raise RuntimeError("attach failed")
        if not self.appletbdrm_appears:
            self.usb_configuration = DRM_CONFIG
            self.appletbdrm_loaded = False
            raise RuntimeError("appletbdrm card did not appear")
        self.usb_configuration = DRM_CONFIG
        self.appletbdrm_loaded = True
        cards = [card for card in self.list_drm_cards() if card.driver == "appletbdrm"]
        return cards[0]

    def restore_firmware_row(self) -> FirmwareRow:
        self.operations.append("restore")
        self.usb_configuration = FIRMWARE_CONFIG
        self.special_key_mode = OBSERVED_BASELINE.special_key_mode
        self.fn_toggle = OBSERVED_BASELINE.fn_toggle
        self.autodim = OBSERVED_BASELINE.autodim
        self.brightness = OBSERVED_BASELINE.brightness
        self.appletbdrm_loaded = False
        return self.firmware_row()

    def acquire_lock(self) -> None:
        self.operations.append("lock")
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise RuntimeError("already owned") from exc
        os.write(fd, b"owned\n")
        os.fsync(fd)
        self._lock_fd = fd

    def release_lock(self) -> None:
        if self._lock_fd is None:
            return
        self.operations.append("unlock")
        fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        os.close(self._lock_fd)
        self._lock_fd = None
        self.lock_path.unlink(missing_ok=True)

    def record_opened_drm(self, card: DrmCard) -> None:
        self.operations.append(f"open:{card.path}")
        self.opened_drm_cards.append(card.path)

    def open_display(self, card: DrmCard) -> FakeDisplaySession:
        if card.driver != "appletbdrm":
            raise RuntimeError("refusing non-touchbar DRM card")
        self.record_opened_drm(card)
        session = FakeDisplaySession(
            card,
            self.presented_surfaces,
            self.presented_frames,
            self.closed_sessions,
        )
        self._display = session
        return session

    def open_touch(self, device: InputDevice) -> FakeTouchSession:
        if not device.is_touchbar_digitizer:
            raise RuntimeError("firmware keyboard is not a touch surface")
        session = FakeTouchSession(
            self.queued_touch_events,
            self.closed_sessions,
            self.touch_read_timeouts,
        )
        self._touch = session
        return session

    def open_backlight(self) -> FakeResourceSession:
        session = FakeResourceSession(self.closed_sessions, "backlight")
        self._backlight = session
        return session

    def open_fn_input(self, device: InputDevice) -> FakeFnSession:
        if not device.is_internal_keyboard:
            raise RuntimeError("input device is not the Apple internal KEY_FN keyboard")
        session = FakeFnSession(self.queued_fn_events, self.closed_sessions)
        self._fn_input = session
        return session

    def wait_for_input(
        self,
        _fn_session: FakeFnSession,
        _touch_session: FakeTouchSession,
        timeout: float,
    ) -> None:
        self.input_wait_timeouts.append(timeout)

    def open_virtual_keyboard(self) -> FakeVirtualKeyboardSession:
        session = FakeVirtualKeyboardSession(
            self.emitted_media_actions,
            self.closed_sessions,
        )
        self._virtual_keyboard = session
        return session
