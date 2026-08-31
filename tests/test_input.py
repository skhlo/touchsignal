from __future__ import annotations

import os
import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from unittest.mock import patch

from touchbar_owner.input import (
    EVENT_FORMAT,
    EV_KEY,
    EV_SYN,
    MEDIA_KEY_CODES,
    UI_DEV_CREATE,
    UI_DEV_DESTROY,
    UI_DEV_SETUP,
    UI_SET_EVBIT,
    UI_SET_KEYBIT,
    UInputSetup,
    LiveFnSession,
    open_virtual_keyboard,
)
from touchbar_owner.host import FakeHost
from touchbar_owner.live import LiveHost
from touchbar_owner.owner import OwnerError, TouchBarOwner
from touchbar_owner.types import (
    KEY_FN,
    OBSERVED_BASELINE,
    FnEvent,
    InputDevice,
    MediaAction,
)


EXPECTED_MEDIA_KEY_CODES = {
    MediaAction.BRIGHTNESS_DOWN: 224,
    MediaAction.BRIGHTNESS_UP: 225,
    MediaAction.PREVIOUS: 165,
    MediaAction.PLAY_PAUSE: 164,
    MediaAction.NEXT: 163,
    MediaAction.VOLUME_DOWN: 114,
    MediaAction.VOLUME_UP: 115,
}


def input_event(event_type: int, code: int, value: int) -> bytes:
    return struct.pack(EVENT_FORMAT, 0, 0, event_type, code, value)


class FdSession:
    def __init__(self, fd: int) -> None:
        self.fd = fd


class InputWaitAdapterTests(unittest.TestCase):
    def test_either_fn_or_touch_readiness_ends_the_idle_wait(self) -> None:
        for ready_session in ("fn", "touch"):
            with self.subTest(ready_session=ready_session):
                fn_read, fn_write = os.pipe()
                touch_read, touch_write = os.pipe()
                try:
                    ready_fd = fn_write if ready_session == "fn" else touch_write
                    os.write(ready_fd, b"ready")
                    host = LiveHost()

                    started = monotonic()
                    host.wait_for_input(
                        FdSession(fn_read),
                        FdSession(touch_read),
                        1.0,
                    )
                    elapsed = monotonic() - started
                finally:
                    for fd in (fn_read, fn_write, touch_read, touch_write):
                        os.close(fd)

                self.assertLess(elapsed, 0.25)


class FnInputAdapterTests(unittest.TestCase):
    def test_fn_reader_is_read_only_ungrabbed_and_normalizes_press_release(self) -> None:
        device = InputDevice(
            name="Apple Inc. Apple Internal Keyboard / Trackpad",
            path="/dev/input/event42",
            bus="0003",
            vendor="05ac",
            product="0340",
            key_codes=frozenset({KEY_FN}),
        )
        payload = b"".join(
            (
                input_event(EV_KEY, KEY_FN, 1),
                input_event(EV_KEY, KEY_FN, 2),
                input_event(EV_KEY, 30, 1),
                input_event(EV_KEY, KEY_FN, 0),
            )
        )

        with (
            patch("touchbar_owner.input.os.open", return_value=12) as open_fd,
            patch(
                "touchbar_owner.input.select.select",
                side_effect=[([12], [], []), ([], [], [])],
            ),
            patch("touchbar_owner.input.os.read", return_value=payload),
            patch("touchbar_owner.input.fcntl.ioctl") as ioctl,
            patch("touchbar_owner.input.os.close") as close_fd,
        ):
            session = LiveFnSession(device)
            events = session.read_events()
            session.close()

        open_fd.assert_called_once_with(
            "/dev/input/event42",
            os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC,
        )
        ioctl.assert_not_called()
        close_fd.assert_called_once_with(12)
        self.assertEqual(events, [FnEvent("press"), FnEvent("release")])


class VirtualKeyboardAdapterTests(unittest.TestCase):
    def test_modern_uinput_keyboard_exposes_only_approved_keys_and_emits_key_pair(self) -> None:
        ioctl_calls: list[tuple[object, ...]] = []
        writes: list[bytes] = []

        def ioctl(*args):
            ioctl_calls.append(args)
            return 0

        def write(_fd: int, payload: bytes) -> int:
            writes.append(payload)
            return len(payload)

        with (
            patch("touchbar_owner.input.os.open", return_value=19) as open_fd,
            patch("touchbar_owner.input.fcntl.ioctl", side_effect=ioctl),
            patch("touchbar_owner.input.os.write", side_effect=write),
            patch("touchbar_owner.input.os.close") as close_fd,
        ):
            keyboard = open_virtual_keyboard()
            keyboard.emit(MediaAction.PLAY_PAUSE)
            keyboard.close()

        open_fd.assert_called_once_with(
            "/dev/uinput",
            os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC,
        )
        configured_keys = {
            args[2]
            for args in ioctl_calls
            if len(args) == 3 and args[1] == UI_SET_KEYBIT
        }
        self.assertEqual(MEDIA_KEY_CODES, EXPECTED_MEDIA_KEY_CODES)
        self.assertEqual(configured_keys, set(EXPECTED_MEDIA_KEY_CODES.values()))
        self.assertIn((19, UI_SET_EVBIT, EV_KEY), ioctl_calls)
        setup_call = next(args for args in ioctl_calls if args[1] == UI_DEV_SETUP)
        setup = UInputSetup.from_buffer_copy(setup_call[2])
        self.assertEqual(setup.id.bustype, 0x06)
        self.assertEqual(bytes(setup.name).rstrip(b"\0"), b"TouchSignal media keys")
        self.assertIn((19, UI_DEV_CREATE), ioctl_calls)
        self.assertIn((19, UI_DEV_DESTROY), ioctl_calls)
        close_fd.assert_called_once_with(19)

        emitted = [
            struct.unpack_from(EVENT_FORMAT, writes[0], offset)[2:]
            for offset in range(0, len(writes[0]), struct.calcsize(EVENT_FORMAT))
        ]
        self.assertEqual(
            emitted,
            [
                (EV_KEY, 164, 1),
                (EV_SYN, 0, 0),
                (EV_KEY, 164, 0),
                (EV_SYN, 0, 0),
            ],
        )


class PhysicalOwnerInputTests(unittest.TestCase):
    def test_virtual_keyboard_setup_failure_cleans_up_every_acquired_resource(self) -> None:
        class FailingVirtualKeyboardHost(FakeHost):
            def open_virtual_keyboard(self):
                raise RuntimeError("uinput setup failed")

        with TemporaryDirectory() as raw:
            host = FailingVirtualKeyboardHost(Path(raw))
            owner = TouchBarOwner(host)

            with self.assertRaisesRegex(OwnerError, "uinput setup failed"):
                owner.claim()

            self.assertEqual(host.firmware_row(), OBSERVED_BASELINE)
            self.assertEqual(
                set(host.closed_sessions),
                {"display", "touch", "backlight", "fn_input"},
            )
            self.assertFalse(host.lock_path.exists())

    def test_missing_stable_fn_keyboard_refuses_before_touchbar_attach(self) -> None:
        class MissingFnHost(FakeHost):
            def list_input_devices(self) -> list[InputDevice]:
                return [
                    device
                    for device in super().list_input_devices()
                    if not device.is_internal_keyboard
                ]

        with TemporaryDirectory() as raw:
            host = MissingFnHost(Path(raw))
            owner = TouchBarOwner(host)

            with self.assertRaisesRegex(OwnerError, "KEY_FN"):
                owner.claim()

            self.assertNotIn("attach", host.operations)
            self.assertEqual(host.firmware_row(), OBSERVED_BASELINE)
            self.assertFalse(host.lock_path.exists())

    def test_owner_normalizes_fn_dispatches_intents_and_closes_both_keyboards(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw))
            host.queued_fn_events = [FnEvent("press")]
            owner = TouchBarOwner(host)
            owner.claim()
            try:
                runtime_input = owner.drain_input()
                owner.dispatch_media_action(MediaAction.VOLUME_DOWN)

                self.assertTrue(runtime_input.fn_held)
                self.assertEqual(host.emitted_media_actions, [MediaAction.VOLUME_DOWN])
                self.assertEqual(
                    owner.state.claimed,
                    {
                        "display",
                        "touch",
                        "backlight",
                        "fn_input",
                        "virtual_keyboard",
                    },
                )
            finally:
                owner.release()

            self.assertEqual(
                set(host.closed_sessions),
                {
                    "display",
                    "touch",
                    "backlight",
                    "fn_input",
                    "virtual_keyboard",
                },
            )

    def test_owner_keeps_fn_held_until_a_later_release_event(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw))
            host.queued_fn_events.append(FnEvent("press"))
            owner = TouchBarOwner(host)
            owner.claim()
            try:
                self.assertTrue(owner.drain_input().fn_held)

                host.queued_fn_events.append(FnEvent("release"))

                self.assertFalse(owner.drain_input().fn_held)
            finally:
                owner.release()


if __name__ == "__main__":
    unittest.main()
