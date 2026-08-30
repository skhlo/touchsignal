from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from time import sleep as default_sleep
from typing import Protocol

from .host import Host
from .owner import OwnerError, TouchBarOwner
from .types import (
    COMPETING_RENDERERS,
    DRM_CONFIG,
    NATIVE_HEIGHT,
    NATIVE_WIDTH,
    RuntimeAction,
    RuntimeFrame,
    RuntimeInput,
    RuntimeResult,
    RuntimeState,
    SUPPORTED_HARDWARE_MODEL,
    firmware_row_restored,
)


class SupervisedRuntimeError(RuntimeError):
    pass


class PreflightError(RuntimeError):
    pass


class Renderer(Protocol):
    def render(self, runtime_input: RuntimeInput) -> RuntimeResult: ...


class ProofRenderer:
    def render(self, runtime_input: RuntimeInput) -> RuntimeResult:
        last_touch = runtime_input.touches[-1] if runtime_input.touches else None
        return RuntimeResult(
            frame=RuntimeFrame(
                surface_size=(NATIVE_WIDTH, NATIVE_HEIGHT),
                touch_count=len(runtime_input.touches),
                last_touch=last_touch,
            )
        )


@dataclass
class SupervisedRuntime:
    host: Host
    owner_factory: Callable[[Host], TouchBarOwner] = TouchBarOwner
    renderer: Renderer = field(default_factory=ProofRenderer)
    wait: Callable[[float], None] = default_sleep
    restart_delay: float = 1.0
    state: RuntimeState = field(default_factory=RuntimeState)
    _owner: TouchBarOwner | None = None
    _last_presented_frame: RuntimeFrame | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def preflight(self) -> None:
        model = self.host.hardware_model()
        if model != SUPPORTED_HARDWARE_MODEL:
            raise PreflightError(
                f"unsupported hardware {model or 'unknown'}, expected {SUPPORTED_HARDWARE_MODEL}"
            )
        if not self.host.graphical_session_ready():
            raise PreflightError("supported graphical session is not ready")
        missing_modules = self.host.missing_kernel_modules()
        if missing_modules:
            raise PreflightError("missing kernel modules: " + ", ".join(missing_modules))
        missing_permissions = self.host.missing_permissions()
        if missing_permissions:
            raise PreflightError("missing permissions: " + ", ".join(missing_permissions))
        competing = [
            name
            for name in self.host.competing_renderers()
            if any(marker in name for marker in COMPETING_RENDERERS)
        ]
        if competing:
            raise PreflightError(
                f"refusing to claim the Touch Bar while {competing[0]} is already running"
            )

        row = self.host.firmware_row()
        if firmware_row_restored(row, self.host.baseline_firmware_row()):
            return
        if row.usb_configuration == DRM_CONFIG or row.appletbdrm_loaded:
            return
        raise PreflightError("firmware row is not at the observed baseline")

    def start(self) -> None:
        if self.state.running:
            return
        self.preflight()
        owner = self.owner_factory(self.host)
        try:
            owner.claim()
        except Exception as exc:
            raise SupervisedRuntimeError(str(exc)) from exc
        self._owner = owner
        self._last_presented_frame = None
        self.state.running = True
        try:
            self._record_result(RuntimeInput())
        except Exception:
            self.stop()
            raise

    def process_once(self) -> None:
        if self._owner is None or not self.state.running:
            raise SupervisedRuntimeError("runtime is not running")
        if not self.host.graphical_session_ready():
            self.stop()
            return
        runtime_input = self._owner.drain_input()
        self.state.touch_events.extend(runtime_input.touches)
        self._record_result(runtime_input)

    def stop(self) -> None:
        owner = self._owner
        self._owner = None
        self._last_presented_frame = None
        self.state.running = False
        if owner is None:
            return
        try:
            owner.release()
        except OwnerError as exc:
            raise SupervisedRuntimeError(str(exc)) from exc

    def run_supervised(self, cycles: int | None = None, max_restarts: int = 1) -> None:
        completed = 0
        while cycles is None or completed < cycles:
            try:
                self.start()
                self.process_once()
                completed += 1
            except PreflightError:
                raise
            except Exception as exc:
                self.state.failures.append(str(exc))
                self._release_after_failure()
                if self.state.restarts >= max_restarts:
                    raise SupervisedRuntimeError(str(exc)) from exc
                self.state.restarts += 1
                self.wait(self.restart_delay)

    def _release_after_failure(self) -> None:
        try:
            self.stop()
        except SupervisedRuntimeError as exc:
            self.state.failures.append(str(exc))

    def _record_result(self, runtime_input: RuntimeInput) -> None:
        if self._owner is None:
            raise SupervisedRuntimeError("runtime is not running")
        result = self.renderer.render(runtime_input)
        frame = result.frame
        if not _same_visual_frame(frame, self._last_presented_frame):
            self._owner.present_frame(frame)
            self._last_presented_frame = frame
            self.state.frames.append(frame)
        elif self.state.frames:
            self.state.frames[-1] = frame
        else:
            self.state.frames.append(frame)
        last_touch = runtime_input.touches[-1] if runtime_input.touches else None
        for intent in result.intents:
            self._owner.dispatch_media_action(intent)
            self.state.actions.append(RuntimeAction(intent.value, last_touch))


def _same_visual_frame(
    current: RuntimeFrame,
    previous: RuntimeFrame | None,
) -> bool:
    if previous is None:
        return False
    return (
        current.surface_size == previous.surface_size
        and current.workflow_frame == previous.workflow_frame
    )
