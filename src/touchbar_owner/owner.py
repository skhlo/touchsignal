from __future__ import annotations

from dataclasses import dataclass, field

from .host import DisplaySession, Host, ResourceSession, TouchSession
from .types import (
    COMPETING_RENDERERS,
    NATIVE_HEIGHT,
    NATIVE_WIDTH,
    OwnerState,
    TouchEvent,
    firmware_row_restored,
)


class OwnerError(RuntimeError):
    pass


@dataclass
class TouchBarOwner:
    host: Host
    state: OwnerState = field(default_factory=OwnerState)
    _display: DisplaySession | None = None
    _touch: TouchSession | None = None
    _backlight: ResourceSession | None = None
    _lock_held: bool = False

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

        digitizers = [
            device
            for device in self.host.list_touch_devices()
            if device.is_touchbar_digitizer
        ]
        if not digitizers:
            raise OwnerError("Touch Bar digitizer did not appear after attach")
        self._touch = self.host.open_touch(digitizers[0])
        self.state.claimed.add("touch")

        self._backlight = self.host.open_backlight()
        self.state.claimed.add("backlight")

        self._display.present_test_surface()

    def release(self) -> None:
        errors: list[str] = []
        for name, session in (
            ("display", self._display),
            ("touch", self._touch),
            ("backlight", self._backlight),
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

    def drain_touch(self) -> list[TouchEvent]:
        if self._touch is None:
            raise OwnerError("touch is not claimed")
        events = self._touch.read_events()
        self.state.touch_events.extend(events)
        return events
