from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from time import monotonic
from typing import Protocol

from .types import RuntimeInput, RuntimeResult


PIXEL_SHIFT_PATTERN = (
    (0, 0),
    (1, 0),
    (1, 1),
    (0, 1),
    (-1, 1),
    (-1, 0),
    (-1, -1),
    (0, -1),
    (1, -1),
)


class Renderer(Protocol):
    def render(self, runtime_input: RuntimeInput) -> RuntimeResult: ...


class CappedReconnectBackoff:
    def __init__(
        self,
        *,
        initial: float,
        maximum: float,
        stable_after: float,
    ) -> None:
        if initial <= 0:
            raise ValueError("initial reconnect delay must be positive")
        if maximum < initial:
            raise ValueError("maximum reconnect delay must not be smaller than initial")
        if stable_after < 0:
            raise ValueError("stable reconnect interval must not be negative")
        self.initial = initial
        self.maximum = maximum
        self.stable_after = stable_after
        self._next = initial

    def next_delay(self, connected_for: float | None = None) -> float:
        if connected_for is not None and connected_for >= self.stable_after:
            self.reset()
        delay = self._next
        self._next = min(delay * 2, self.maximum)
        return delay

    def reset(self) -> None:
        self._next = self.initial


class PixelShiftRenderer:
    def __init__(
        self,
        renderer: Renderer,
        *,
        clock: Callable[[], float] = monotonic,
        cadence: float = 60.0,
    ) -> None:
        if cadence <= 0:
            raise ValueError("pixel-shift cadence must be positive")
        self.renderer = renderer
        self.clock = clock
        self.cadence = cadence
        self._started_at: float | None = None

    def render(self, runtime_input: RuntimeInput) -> RuntimeResult:
        now = self.clock()
        if self._started_at is None:
            self._started_at = now
        elapsed = max(0.0, now - self._started_at)
        index = int(elapsed // self.cadence) % len(PIXEL_SHIFT_PATTERN)
        result = self.renderer.render(runtime_input)
        frame = replace(
            result.frame,
            content_offset=PIXEL_SHIFT_PATTERN[index],
        )
        return replace(result, frame=frame)
