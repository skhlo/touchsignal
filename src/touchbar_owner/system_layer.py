from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from typing import Protocol

from .types import (
    NATIVE_HEIGHT,
    NATIVE_WIDTH,
    MediaAction,
    RuntimeFrame,
    RuntimeInput,
    RuntimeResult,
    TouchEvent,
)
from .workflow import TOUCH_SLOP, Geometry


MEDIA_ACTIONS = tuple(MediaAction)
MEDIA_LABELS = {
    MediaAction.BRIGHTNESS_DOWN: "BRIGHT -",
    MediaAction.BRIGHTNESS_UP: "BRIGHT +",
    MediaAction.PREVIOUS: "PREVIOUS",
    MediaAction.PLAY_PAUSE: "PLAY / PAUSE",
    MediaAction.NEXT: "NEXT",
    MediaAction.VOLUME_DOWN: "VOLUME -",
    MediaAction.VOLUME_UP: "VOLUME +",
}
MEDIA_TARGET_HEIGHT = 46
MEDIA_TARGET_GAP = 4
MEDIA_TARGET_MARGIN = 4


class LockState(StrEnum):
    UNLOCKED = "unlocked"
    LOCKED = "locked"
    UNAVAILABLE = "unavailable"


class LockStateSource(Protocol):
    def state(self) -> LockState: ...


class LiveOmarchyLockSource:
    def __init__(
        self,
        *,
        run=subprocess.run,
        timeout: float = 0.5,
        clock=monotonic,
        poll_interval: float = 0.25,
    ) -> None:
        self.run = run
        self.timeout = timeout
        self.clock = clock
        self.poll_interval = poll_interval
        self._checked_at: float | None = None
        self._cached_state = LockState.UNAVAILABLE

    def state(self) -> LockState:
        now = self.clock()
        if (
            self._checked_at is not None
            and self._cached_state != LockState.UNLOCKED
            and now >= self._checked_at
            and now - self._checked_at < self.poll_interval
        ):
            return self._cached_state
        self._cached_state = self._query_state()
        self._checked_at = self.clock()
        return self._cached_state

    def _query_state(self) -> LockState:
        environment = os.environ.copy()
        environment["OMARCHY_SHELL_IPC_TIMEOUT"] = f"{self.timeout:g}s"
        try:
            completed = self.run(
                ["omarchy-shell", "lock", "isLocked"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.timeout,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError):
            return LockState.UNAVAILABLE
        result = completed.stdout.strip()
        if result == "false":
            return LockState.UNLOCKED
        if result == "true":
            return LockState.LOCKED
        return LockState.UNAVAILABLE


class AgentWorkflow(Protocol):
    def process_touch_events(self, events: list[TouchEvent]) -> None: ...

    def frame(self) -> object: ...

    def cancel_contacts(self) -> None: ...

    def refresh_verified_state(self) -> None: ...


@dataclass(frozen=True)
class MediaButtonFrame:
    action: MediaAction
    target: Geometry
    pressed: bool = False
    touch_cancelled: bool = False


@dataclass(frozen=True)
class MediaLayerFrame:
    surface_size: tuple[int, int]
    buttons: tuple[MediaButtonFrame, ...]
    privacy_safe: bool = True


@dataclass(frozen=True)
class MediaPressState:
    action: MediaAction
    eligible: bool


class SystemLayerWorkflow:
    def __init__(
        self,
        agents: AgentWorkflow,
        lock_source: LockStateSource,
    ) -> None:
        self.agents = agents
        self.lock_source = lock_source
        self._active_layer: str | None = None
        self._media_press: MediaPressState | None = None

    def render(self, runtime_input: RuntimeInput) -> RuntimeResult:
        locked = self.lock_source.state() != LockState.UNLOCKED
        desired_layer = "media" if locked or runtime_input.fn_held else "agents"
        layer_changed = False
        accept_transition_touches = False
        if self._active_layer is None:
            self._active_layer = desired_layer
        elif self._active_layer != desired_layer:
            accept_transition_touches = (
                desired_layer == "media" and runtime_input.fn_held and not locked
            )
            self._transition_to(desired_layer)
            layer_changed = True

        if layer_changed and not accept_transition_touches:
            intents = ()
        elif self._active_layer == "media":
            intents = self._process_media_events(runtime_input.touches)
        else:
            intents = ()
            self.agents.process_touch_events(list(runtime_input.touches))

        layer: object = (
            self._media_frame()
            if self._active_layer == "media"
            else self.agents.frame()
        )
        frame = RuntimeFrame(
            surface_size=(NATIVE_WIDTH, NATIVE_HEIGHT),
            touch_count=len(runtime_input.touches),
            last_touch=runtime_input.touches[-1] if runtime_input.touches else None,
            workflow_frame=layer,
        )
        return RuntimeResult(frame=frame, intents=intents)

    def _transition_to(self, layer: str) -> None:
        self.agents.cancel_contacts()
        self._media_press = None
        if layer == "agents":
            self.agents.refresh_verified_state()
        self._active_layer = layer

    def _process_media_events(
        self,
        events: tuple[TouchEvent, ...],
    ) -> tuple[MediaAction, ...]:
        intents: list[MediaAction] = []
        for event in events:
            if event.kind == "down":
                self._media_press = self._press_at(event)
            elif event.kind == "move" and self._media_press is not None:
                target = media_target_geometry(
                    MEDIA_ACTIONS.index(self._media_press.action)
                )
                self._media_press = MediaPressState(
                    action=self._media_press.action,
                    eligible=target.contains(event.x, event.y, slop=TOUCH_SLOP),
                )
            elif event.kind == "up" and self._media_press is not None:
                target = media_target_geometry(
                    MEDIA_ACTIONS.index(self._media_press.action)
                )
                action = self._media_press.action
                eligible = target.contains(event.x, event.y, slop=TOUCH_SLOP)
                self._media_press = None
                if eligible:
                    intents.append(action)
        return tuple(intents)

    def _press_at(self, event: TouchEvent) -> MediaPressState | None:
        for index, action in enumerate(MEDIA_ACTIONS):
            if media_target_geometry(index).contains(event.x, event.y):
                return MediaPressState(action=action, eligible=True)
        return None

    def _media_frame(self) -> MediaLayerFrame:
        return MediaLayerFrame(
            surface_size=(NATIVE_WIDTH, NATIVE_HEIGHT),
            buttons=tuple(
                MediaButtonFrame(
                    action=action,
                    target=media_target_geometry(index),
                    pressed=(
                        self._media_press is not None
                        and self._media_press.action == action
                        and self._media_press.eligible
                    ),
                    touch_cancelled=(
                        self._media_press is not None
                        and self._media_press.action == action
                        and not self._media_press.eligible
                    ),
                )
                for index, action in enumerate(MEDIA_ACTIONS)
            ),
        )


def media_target_geometry(index: int) -> Geometry:
    if not 0 <= index < len(MEDIA_ACTIONS):
        raise ValueError(f"media button index {index} is out of range")
    available = (
        NATIVE_WIDTH
        - MEDIA_TARGET_MARGIN * 2
        - MEDIA_TARGET_GAP * (len(MEDIA_ACTIONS) - 1)
    )
    base_width, remainder = divmod(available, len(MEDIA_ACTIONS))
    width = base_width + (1 if index < remainder else 0)
    x = (
        MEDIA_TARGET_MARGIN
        + index * (base_width + MEDIA_TARGET_GAP)
        + min(index, remainder)
    )
    return Geometry(
        x=x,
        y=(NATIVE_HEIGHT - MEDIA_TARGET_HEIGHT) // 2,
        width=width,
        height=MEDIA_TARGET_HEIGHT,
    )
