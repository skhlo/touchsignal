from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from touchbar_owner.host import FakeHost
from touchbar_owner.owner import OwnerError, TouchBarOwner
from touchbar_owner.types import OBSERVED_BASELINE


class RestoreAndCleanupTests(unittest.TestCase):
    def test_normal_stop_releases_every_acquired_device_and_restores_the_firmware_row(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw))
            owner = TouchBarOwner(host)
            owner.claim()
            self.assertEqual(host.usb_configuration, "2")
            owner.release()

            self.assertEqual(host.firmware_row(), OBSERVED_BASELINE)
            self.assertEqual(
                set(host.closed_sessions),
                {"display", "touch", "backlight", "virtual_input"},
            )
            self.assertFalse(host.lock_path.exists())

            successor = TouchBarOwner(host)
            successor.claim()
            successor.release()

    def test_failed_attachment_restores_the_firmware_row_immediately(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw), attach_succeeds=False)
            owner = TouchBarOwner(host)

            with self.assertRaisesRegex(OwnerError, "attach failed"):
                owner.claim()

            self.assertEqual(host.firmware_row(), OBSERVED_BASELINE)
            self.assertEqual(owner.state.claimed, set())
            self.assertFalse(host.lock_path.exists())

    def test_failed_appletbdrm_probe_does_not_leave_config_two_active(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw), appletbdrm_appears=False)
            owner = TouchBarOwner(host)

            with self.assertRaises(OwnerError):
                owner.claim()

            self.assertEqual(host.usb_configuration, "1")
            self.assertFalse(host.appletbdrm_loaded)
            self.assertEqual(host.firmware_row(), OBSERVED_BASELINE)
