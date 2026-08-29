from __future__ import annotations

import os
import select
import struct
from pathlib import Path

from .types import TOUCH_MAX_X, TOUCH_MAX_Y, NATIVE_HEIGHT, NATIVE_WIDTH, TouchDevice, TouchEvent

EV_SYN = 0x00
EV_ABS = 0x03
SYN_REPORT = 0
ABS_MT_SLOT = 0x2F
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36
ABS_MT_TRACKING_ID = 0x39
EVENT_FORMAT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)


def scale_touch(raw_x: int, raw_y: int, width: int = NATIVE_WIDTH, height: int = NATIVE_HEIGHT) -> tuple[int, int]:
    x = round(raw_x * (width - 1) / TOUCH_MAX_X)
    y = round(raw_y * (height - 1) / TOUCH_MAX_Y)
    return x, y


class LiveTouchSession:
    def __init__(self, device: TouchDevice) -> None:
        self.device = device
        self.fd = os.open(device.path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        self.closed = False
        self._cur_x = 0
        self._cur_y = 0
        self._cur_slot = 0
        self._primary_slot = -1
        self._touch_active = False
        self._touch_starting = False
        self._pos_dirty = False

    def read_events(self, timeout: float = 0.0) -> list[TouchEvent]:
        if self.closed:
            raise RuntimeError("touch already closed")
        events: list[TouchEvent] = []
        remaining = timeout
        while True:
            ready, _, _ = select.select([self.fd], [], [], remaining)
            if not ready:
                break
            remaining = 0.0
            chunk = os.read(self.fd, EVENT_SIZE * 64)
            if not chunk:
                break
            for offset in range(0, len(chunk), EVENT_SIZE):
                _sec, _usec, ev_type, code, value = struct.unpack_from(EVENT_FORMAT, chunk, offset)
                event = self._consume(ev_type, code, value)
                if event is not None:
                    events.append(event)
        return events

    def _consume(self, ev_type: int, code: int, value: int) -> TouchEvent | None:
        if ev_type == EV_ABS and code == ABS_MT_SLOT:
            self._cur_slot = value
            return None
        if ev_type == EV_ABS and code == ABS_MT_TRACKING_ID:
            if value >= 0:
                if self._primary_slot < 0:
                    self._primary_slot = self._cur_slot
                    self._touch_active = True
                    self._touch_starting = True
                    self._pos_dirty = False
            elif self._cur_slot == self._primary_slot:
                self._primary_slot = -1
                self._touch_active = False
                self._touch_starting = False
                self._pos_dirty = False
                x, y = scale_touch(self._cur_x, self._cur_y)
                return TouchEvent("up", x, y)
            return None
        if ev_type == EV_ABS and code == ABS_MT_POSITION_X and self._cur_slot == self._primary_slot:
            self._cur_x = value
            if self._touch_active:
                self._pos_dirty = True
            return None
        if ev_type == EV_ABS and code == ABS_MT_POSITION_Y and self._cur_slot == self._primary_slot:
            self._cur_y = value
            if self._touch_active:
                self._pos_dirty = True
            return None
        if ev_type == EV_SYN and code == SYN_REPORT:
            if self._touch_starting:
                self._touch_starting = False
                self._pos_dirty = False
                x, y = scale_touch(self._cur_x, self._cur_y)
                return TouchEvent("down", x, y)
            if self._touch_active and self._pos_dirty:
                self._pos_dirty = False
                x, y = scale_touch(self._cur_x, self._cur_y)
                return TouchEvent("move", x, y)
        return None

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        os.close(self.fd)


class LiveResourceSession:
    def __init__(self, path: str, fd: int | None = None) -> None:
        self.path = path
        self.fd = fd
        self.closed = False
        self.previous: str | None = None

    def set_value(self, value: str) -> str:
        if self.fd is None:
            raise RuntimeError(f"{self.path} is not writable")
        current = Path(self.path).read_text(encoding="utf-8", errors="replace").strip()
        if self.previous is None:
            self.previous = current
        if current != value:
            os.lseek(self.fd, 0, os.SEEK_SET)
            os.write(self.fd, (value if value.endswith("\n") else value + "\n").encode())
        return current

    def restore_previous(self) -> None:
        if self.fd is None or self.previous is None:
            return
        os.lseek(self.fd, 0, os.SEEK_SET)
        os.write(self.fd, (self.previous + "\n").encode())

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.restore_previous()
        finally:
            if self.fd is not None:
                os.close(self.fd)


def open_virtual_keyboard() -> LiveResourceSession:
    path = "/dev/uinput"
    fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    return LiveResourceSession(path, fd)


def open_backlight() -> LiveResourceSession:
    path = "/sys/class/backlight/appletb_backlight/brightness"
    if not Path(path).exists():
        raise RuntimeError("appletb_backlight was not found")
    fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
    return LiveResourceSession(path, fd)
