from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from touchbar_owner.cli import main
from touchbar_owner.drm import copy_drm_mode, drmModeModeInfo
from touchbar_owner.host import FakeHost
from touchbar_owner.owner import OwnerError, TouchBarOwner
from touchbar_owner.types import OBSERVED_BASELINE


class DelayedDeviceHost(FakeHost):
    def __init__(self, root: Path, touch_after: int = 0, backlight_after: int = 0) -> None:
        super().__init__(root)
        self.touch_after = touch_after
        self.backlight_after = backlight_after
        self.touch_lookups = 0
        self.backlight_lookups = 0

    def list_touch_devices(self):
        devices = super().list_touch_devices()
        self.touch_lookups += 1
        if self.touch_lookups <= self.touch_after:
            return [device for device in devices if not device.is_touchbar_digitizer]
        return devices

    def open_backlight(self):
        self.backlight_lookups += 1
        if self.backlight_lookups <= self.backlight_after:
            raise RuntimeError("appletb_backlight was not found")
        return super().open_backlight()


class DrmModeCopyTests(unittest.TestCase):
    def test_copied_mode_survives_after_the_source_is_cleared(self) -> None:
        source = drmModeModeInfo()
        source.hdisplay = 60
        source.vdisplay = 2008
        source.name = b"60x2008"
        copied = copy_drm_mode(source)
        source.hdisplay = 0
        source.vdisplay = 0
        source.name = b""
        self.assertEqual(copied.hdisplay, 60)
        self.assertEqual(copied.vdisplay, 2008)
        self.assertEqual(copied.name, b"60x2008")


class RediscoveryTests(unittest.TestCase):
    def test_claim_waits_for_a_late_digitizer_and_backlight(self) -> None:
        with TemporaryDirectory() as raw:
            host = DelayedDeviceHost(Path(raw), touch_after=1, backlight_after=1)
            owner = TouchBarOwner(host, wait=lambda _seconds: None, timeout=2.0)
            owner.claim()
            try:
                self.assertEqual(
                    owner.state.claimed,
                    {"display", "touch", "backlight", "virtual_input"},
                )
                self.assertGreater(host.touch_lookups, 1)
                self.assertGreater(host.backlight_lookups, 1)
            finally:
                owner.release()

    def test_digitizer_timeout_restores_usb_configuration_one(self) -> None:
        with TemporaryDirectory() as raw:
            host = DelayedDeviceHost(Path(raw), touch_after=100)
            owner = TouchBarOwner(host, wait=lambda _seconds: None, timeout=0.0)
            with self.assertRaisesRegex(OwnerError, "digitizer"):
                owner.claim()
            self.assertEqual(host.usb_configuration, "1")
            self.assertEqual(host.firmware_row(), OBSERVED_BASELINE)
            self.assertFalse(host.lock_path.exists())

    def test_backlight_timeout_restores_usb_configuration_one(self) -> None:
        with TemporaryDirectory() as raw:
            host = DelayedDeviceHost(Path(raw), backlight_after=100)
            owner = TouchBarOwner(host, wait=lambda _seconds: None, timeout=0.0)
            with self.assertRaisesRegex(OwnerError, "backlight"):
                owner.claim()
            self.assertEqual(host.usb_configuration, "1")
            self.assertEqual(host.firmware_row(), OBSERVED_BASELINE)


class CliFailureTests(unittest.TestCase):
    def test_claim_reports_permission_error_without_traceback(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw))

            def fail_claim(self: TouchBarOwner) -> None:
                raise PermissionError("Permission denied: /dev/uinput")

            stderr = io.StringIO()
            stdout = io.StringIO()
            with patch("touchbar_owner.cli.LiveHost", return_value=host):
                with patch.object(TouchBarOwner, "claim", fail_claim):
                    with patch("touchbar_owner.cli.restore_firmware_row", return_value=OBSERVED_BASELINE):
                        with redirect_stdout(stdout), redirect_stderr(stderr):
                            rc = main(["claim", "--seconds", "0"])
            self.assertEqual(rc, 1)
            self.assertIn("claim failed", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())
            self.assertNotIn("Traceback", stdout.getvalue())

    def test_idle_returns_nonzero_when_release_fails(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw))

            def fail_release(self: TouchBarOwner) -> None:
                raise OwnerError("firmware row did not return to the observed baseline")

            stderr = io.StringIO()
            with patch("touchbar_owner.cli.LiveHost", return_value=host):
                with patch.object(TouchBarOwner, "release", fail_release):
                    with patch("touchbar_owner.cli.restore_firmware_row", return_value=OBSERVED_BASELINE):
                        with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                            rc = main(["idle", "--seconds", "0"])
            self.assertEqual(rc, 1)
            self.assertIn("release failed", stderr.getvalue())
