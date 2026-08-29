from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from touchbar_owner.host import FakeHost
from touchbar_owner.owner import OwnerError, TouchBarOwner


class ExclusiveOwnershipTests(unittest.TestCase):
    def test_claim_refuses_a_second_owner_while_the_first_still_holds_the_bar(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw))
            first = TouchBarOwner(host)
            first.claim()

            second = TouchBarOwner(host)
            with self.assertRaisesRegex(OwnerError, "already owned"):
                second.claim()

            self.assertEqual(
                first.state.claimed,
                {"display", "touch", "backlight", "virtual_input"},
            )
            first.release()

    def test_failed_second_claim_does_not_restore_under_the_first_owner(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw))
            first = TouchBarOwner(host)
            first.claim()
            self.assertEqual(host.usb_configuration, "2")

            second = TouchBarOwner(host)
            with self.assertRaisesRegex(OwnerError, "already owned"):
                second.claim()

            self.assertEqual(host.usb_configuration, "2")
            self.assertEqual(
                first.state.claimed,
                {"display", "touch", "backlight", "virtual_input"},
            )
            first.release()
            self.assertEqual(host.usb_configuration, "1")

    def test_claim_refuses_tiny_dfr_or_react_drm_already_running(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw), competing=("tiny-dfr",))
            owner = TouchBarOwner(host)

            with self.assertRaisesRegex(OwnerError, "tiny-dfr"):
                owner.claim()

            self.assertEqual(host.usb_configuration, "1")
            self.assertEqual(host.firmware_row(), host.baseline_firmware_row())

    def test_claim_refuses_non_touchbar_drm_cards(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw), appletbdrm_appears=False)
            owner = TouchBarOwner(host)

            with self.assertRaisesRegex(OwnerError, "appletbdrm"):
                owner.claim()

            self.assertEqual(host.opened_drm_cards, [])
            self.assertEqual(host.usb_configuration, "1")
            self.assertEqual(host.firmware_row(), host.baseline_firmware_row())
