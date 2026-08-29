from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from touchbar_owner.host import FakeHost
from touchbar_owner.owner import OwnerError, TouchBarOwner
from touchbar_owner.types import NATIVE_HEIGHT, NATIVE_WIDTH, TouchEvent


class NativeSurfaceAndTouchTests(unittest.TestCase):
    def test_claim_presents_a_stable_2008_by_60_test_surface(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw))
            owner = TouchBarOwner(host)
            owner.claim()
            try:
                self.assertEqual(host.presented_surfaces, [(NATIVE_WIDTH, NATIVE_HEIGHT)])
                self.assertEqual(host.opened_drm_cards, ["/dev/dri/card3"])
            finally:
                owner.release()

    def test_owner_reports_touch_down_movement_and_release(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw))
            host.queued_touch_events = [
                TouchEvent("down", 100, 30),
                TouchEvent("move", 400, 30),
                TouchEvent("up", 400, 30),
            ]
            owner = TouchBarOwner(host)
            owner.claim()
            try:
                events = owner.drain_touch()
                self.assertEqual([event.kind for event in events], ["down", "move", "up"])
                self.assertEqual(events[0].x, 100)
                self.assertEqual(events[1].x, 400)
            finally:
                owner.release()

    def test_firmware_keyboard_is_not_treated_as_the_digitizer(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw))
            owner = TouchBarOwner(host)
            owner.claim()
            try:
                names = [device.name for device in host.list_touch_devices()]
                self.assertIn("Apple Inc. Touch Bar Display", names)
                self.assertIn("Apple Inc. Touch Bar Display Touchpad", names)
                events = owner.drain_touch()
                self.assertEqual(events, [])
            finally:
                owner.release()

    def test_wrong_panel_size_is_rejected_before_scanout(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw))

            original = host.attach_display

            def bad_attach():
                card = original()
                return card.__class__(
                    card.name,
                    card.driver,
                    card.path,
                    60,
                    2170,
                    True,
                )

            host.attach_display = bad_attach  # type: ignore[method-assign]
            owner = TouchBarOwner(host)
            with self.assertRaisesRegex(OwnerError, "2008x60"):
                owner.claim()
            self.assertEqual(host.opened_drm_cards, [])
            self.assertEqual(host.firmware_row(), host.baseline_firmware_row())
