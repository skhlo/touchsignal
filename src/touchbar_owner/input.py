from __future__ import annotations

import ctypes
import fcntl
import os
import select
import struct
from pathlib import Path

from .types import (
    KEY_FN,
    TOUCH_MAX_X,
    TOUCH_MAX_Y,
    FnEvent,
    MediaAction,
    NATIVE_HEIGHT,
    NATIVE_WIDTH,
    InputDevice,
    TouchEvent,
)

EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03
SYN_REPORT = 0
ABS_MT_SLOT = 0x2F
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36
ABS_MT_TRACKING_ID = 0x39
EVENT_FORMAT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)

KEY_VOLUMEDOWN = 114
KEY_VOLUMEUP = 115
KEY_NEXTSONG = 163
KEY_PLAYPAUSE = 164
KEY_PREVIOUSSONG = 165
KEY_BRIGHTNESSDOWN = 224
KEY_BRIGHTNESSUP = 225
BUS_VIRTUAL = 0x06

MEDIA_KEY_CODES = {
    MediaAction.BRIGHTNESS_DOWN: KEY_BRIGHTNESSDOWN,
    MediaAction.BRIGHTNESS_UP: KEY_BRIGHTNESSUP,
    MediaAction.PREVIOUS: KEY_PREVIOUSSONG,
    MediaAction.PLAY_PAUSE: KEY_PLAYPAUSE,
    MediaAction.NEXT: KEY_NEXTSONG,
    MediaAction.VOLUME_DOWN: KEY_VOLUMEDOWN,
    MediaAction.VOLUME_UP: KEY_VOLUMEUP,
}

UINPUT_IOCTL_BASE = ord("U")


def _ioc(direction: int, number: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (UINPUT_IOCTL_BASE << 8) | number


def _iow(number: int, size: int) -> int:
    return _ioc(1, number, size)


class InputId(ctypes.Structure):
    _fields_ = [
        ("bustype", ctypes.c_uint16),
        ("vendor", ctypes.c_uint16),
        ("product", ctypes.c_uint16),
        ("version", ctypes.c_uint16),
    ]


class UInputSetup(ctypes.Structure):
    _fields_ = [
        ("id", InputId),
        ("name", ctypes.c_char * 80),
        ("ff_effects_max", ctypes.c_uint32),
    ]


UI_DEV_CREATE = _ioc(0, 1, 0)
UI_DEV_DESTROY = _ioc(0, 2, 0)
UI_DEV_SETUP = _iow(3, ctypes.sizeof(UInputSetup))
UI_SET_EVBIT = _iow(100, ctypes.sizeof(ctypes.c_int))
UI_SET_KEYBIT = _iow(101, ctypes.sizeof(ctypes.c_int))


def scale_touch(raw_x: int, raw_y: int, width: int = NATIVE_WIDTH, height: int = NATIVE_HEIGHT) -> tuple[int, int]:
    x = round(raw_x * (width - 1) / TOUCH_MAX_X)
    y = round(raw_y * (height - 1) / TOUCH_MAX_Y)
    return x, y


class LiveFnSession:
    def __init__(self, device: InputDevice) -> None:
        if not device.is_internal_keyboard:
            raise RuntimeError("input device is not the Apple internal KEY_FN keyboard")
        self.device = device
        self.fd = os.open(
            device.path,
            os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC,
        )
        self.closed = False

    def read_events(self, timeout: float = 0.0) -> list[FnEvent]:
        if self.closed:
            raise RuntimeError("Fn input already closed")
        events: list[FnEvent] = []
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
                _sec, _usec, event_type, code, value = struct.unpack_from(
                    EVENT_FORMAT,
                    chunk,
                    offset,
                )
                if event_type != EV_KEY or code != KEY_FN:
                    continue
                if value == 1:
                    events.append(FnEvent("press"))
                elif value == 0:
                    events.append(FnEvent("release"))
        return events

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        os.close(self.fd)


class LiveTouchSession:
    def __init__(self, device: InputDevice) -> None:
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


class LiveVirtualKeyboardSession:
    def __init__(self, path: str, fd: int) -> None:
        self.path = path
        self.fd = fd
        self.closed = False
        self.created = False

    def create(self) -> None:
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        for key_code in MEDIA_KEY_CODES.values():
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, key_code)
        setup = UInputSetup()
        setup.id = InputId(BUS_VIRTUAL, 0x05AC, 0x0001, 1)
        setup.name = b"TouchSignal media keys"
        fcntl.ioctl(self.fd, UI_DEV_SETUP, bytes(setup))
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        self.created = True

    def emit(self, action: MediaAction) -> None:
        if self.closed or not self.created:
            raise RuntimeError("virtual keyboard is not available")
        key_code = MEDIA_KEY_CODES[action]
        payload = b"".join(
            (
                struct.pack(EVENT_FORMAT, 0, 0, EV_KEY, key_code, 1),
                struct.pack(EVENT_FORMAT, 0, 0, EV_SYN, SYN_REPORT, 0),
                struct.pack(EVENT_FORMAT, 0, 0, EV_KEY, key_code, 0),
                struct.pack(EVENT_FORMAT, 0, 0, EV_SYN, SYN_REPORT, 0),
            )
        )
        offset = 0
        while offset < len(payload):
            written = os.write(self.fd, payload[offset:])
            if written <= 0:
                raise RuntimeError("uinput event write did not make progress")
            offset += written

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            if self.created:
                fcntl.ioctl(self.fd, UI_DEV_DESTROY)
                self.created = False
        finally:
            os.close(self.fd)


def open_virtual_keyboard() -> LiveVirtualKeyboardSession:
    path = "/dev/uinput"
    fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    keyboard = LiveVirtualKeyboardSession(path, fd)
    try:
        keyboard.create()
    except Exception:
        keyboard.close()
        raise
    return keyboard


def open_backlight() -> LiveResourceSession:
    path = "/sys/class/backlight/appletb_backlight/brightness"
    if not Path(path).exists():
        raise RuntimeError("appletb_backlight was not found")
    fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
    return LiveResourceSession(path, fd)
