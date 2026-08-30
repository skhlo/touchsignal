from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic, sleep as default_sleep

from .host import DisplaySession, Host, ResourceSession, TouchSession
from .types import (
    COMPETING_RENDERERS,
    DRM_CONFIG,
    NATIVE_HEIGHT,
    NATIVE_WIDTH,
    OwnerState,
    RuntimeFrame,
    TouchEvent,
    firmware_row_restored,
)


class OwnerError(RuntimeError):
    pass


@dataclass
class TouchBarOwner:
    host: Host
    state: OwnerState = field(default_factory=OwnerState)
    wait: Callable[[float], None] = default_sleep
    timeout: float = 20.0
    _display: DisplaySession | None = None
    _touch: TouchSession | None = None
    _backlight: ResourceSession | None = None
    _virtual_input: ResourceSession | None = None
    _lock_held: bool = False

    def _until(self, missing: str, probe):
        deadline = monotonic() + self.timeout
        while True:
            try:
                value = probe()
            except Exception as exc:
                value = None
                last = exc
            else:
                last = None
                if value:
                    return value
            if monotonic() >= deadline:
                if last is not None:
                    raise OwnerError(str(last)) from last
                raise OwnerError(missing)
            self.wait(0.05)

    def claim(self) -> None:
        try:
            self._claim()
        except Exception:
            self.release()
            raise

    def _claim(self) -> None:
        competing = [
            name
            for name in self.host.competing_renderers()
            if any(marker in name for marker in COMPETING_RENDERERS)
        ]
        if competing:
            raise OwnerError(
                f"refusing to claim the Touch Bar while {competing[0]} is already running"
            )

        try:
            self.host.acquire_lock()
        except RuntimeError as exc:
            raise OwnerError(str(exc)) from exc
        self._lock_held = True

        row = self.host.firmware_row()
        if not firmware_row_restored(row, self.host.baseline_firmware_row()):
            if row.usb_configuration == DRM_CONFIG or row.appletbdrm_loaded:
                restored = self.host.restore_firmware_row()
                if not firmware_row_restored(restored, self.host.baseline_firmware_row()):
                    raise OwnerError("firmware row could not be restored before claim")
            else:
                raise OwnerError("firmware row is not at the observed baseline")

        try:
            card = self.host.attach_display()
        except RuntimeError as exc:
            raise OwnerError(str(exc)) from exc

        if card.driver != "appletbdrm":
            raise OwnerError("appletbdrm card did not appear")

        width, height = card.logical_size
        if (width, height) != (NATIVE_WIDTH, NATIVE_HEIGHT):
            raise OwnerError(
                f"Touch Bar mode is {width}x{height}, expected {NATIVE_WIDTH}x{NATIVE_HEIGHT}"
            )

        self._display = self.host.open_display(card)
        self.state.claimed.add("display")

        digitizers = self._until(
            "Touch Bar digitizer did not appear after attach",
            lambda: [
                device
                for device in self.host.list_touch_devices()
                if device.is_touchbar_digitizer
            ],
        )
        self._touch = self.host.open_touch(digitizers[0])
        self.state.claimed.add("touch")

        self._backlight = self._until(
            "appletb_backlight was not found",
            self.host.open_backlight,
        )
        self.state.claimed.add("backlight")
        set_value = getattr(self._backlight, "set_value", None)
        if callable(set_value):
            set_value("2")

        self._virtual_input = self.host.open_virtual_input()
        self.state.claimed.add("virtual_input")

    def release(self) -> None:
        errors: list[str] = []
        for name, session in (
            ("display", self._display),
            ("touch", self._touch),
            ("backlight", self._backlight),
            ("virtual_input", self._virtual_input),
        ):
            if session is None:
                continue
            try:
                session.close()
            except Exception as exc:  # pragma: no cover - defensive cleanup
                errors.append(f"{name}: {exc}")
        self._display = None
        self._touch = None
        self._backlight = None
        self._virtual_input = None
        self.state.claimed.clear()
        restored = None
        if self._lock_held:
            try:
                restored = self.host.restore_firmware_row()
            except Exception as exc:
                errors.append(f"restore: {exc}")
        if self._lock_held:
            try:
                self.host.release_lock()
            except Exception as exc:
                errors.append(f"lock: {exc}")
            self._lock_held = False
        if restored is not None and not firmware_row_restored(restored, self.host.baseline_firmware_row()):
            errors.append("firmware row did not return to the observed baseline")
        if errors:
            raise OwnerError("; ".join(errors))

    def drain_touch(self, timeout: float = 0.0) -> list[TouchEvent]:
        if self._touch is None:
            raise OwnerError("touch is not claimed")
        events = self._touch.read_events(timeout)
        self.state.touch_events.extend(events)
        return events

    def present_test_surface(self) -> None:
        if self._display is None:
            raise OwnerError("display is not claimed")
        try:
            self._display.present_test_surface()
        except Exception as exc:
            raise OwnerError(str(exc)) from exc

    def present_frame(self, frame: RuntimeFrame) -> None:
        if self._display is None:
            raise OwnerError("display is not claimed")
        try:
            self._display.present_frame(frame)
        except Exception as exc:
            raise OwnerError(str(exc)) from exc
