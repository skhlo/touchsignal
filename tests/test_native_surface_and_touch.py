from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from touchbar_owner.host import FakeHost
from touchbar_owner.owner import OwnerError, TouchBarOwner
from touchbar_owner.surface import copy_logical_to_physical_scanout, map_logical_to_physical, test_surface_plan
from touchbar_owner.types import NATIVE_HEIGHT, NATIVE_WIDTH, TouchEvent


class NativeSurfaceAndTouchTests(unittest.TestCase):
    def test_claim_acquires_display_before_explicit_test_surface_presentation(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw))
            owner = TouchBarOwner(host)
            owner.claim()
            try:
                self.assertEqual(host.presented_surfaces, [])
                owner.present_test_surface()
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
                names = [device.name for device in host.list_input_devices()]
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

    def test_diagnostic_stripes_cover_the_native_canvas(self) -> None:
        plan = test_surface_plan()
        self.assertEqual((plan.canvas_width, plan.canvas_height), (NATIVE_WIDTH, NATIVE_HEIGHT))
        self.assertGreaterEqual(len(plan.stripes), 2)
        self.assertEqual(plan.first_stripe.x, 0)
        self.assertEqual(plan.first_stripe.y, 0)
        self.assertEqual(plan.first_stripe.height, NATIVE_HEIGHT)
        self.assertEqual(plan.last_stripe.right, NATIVE_WIDTH - 1)
        self.assertEqual(plan.last_stripe.bottom, NATIVE_HEIGHT - 1)
        self.assertEqual(plan.first_stripe.color, (0.82, 0.28, 0.28))
        self.assertEqual(plan.stripes[1].color, (1.0, 1.0, 1.0))
        covered = sum(stripe.width for stripe in plan.stripes)
        self.assertEqual(covered, NATIVE_WIDTH)

        physical_width, physical_height = NATIVE_HEIGHT, NATIVE_WIDTH
        left_edge = map_logical_to_physical(
            0,
            0,
            rotate90=True,
            physical_width=physical_width,
            physical_height=physical_height,
        )
        right_edge = map_logical_to_physical(
            NATIVE_WIDTH - 1,
            NATIVE_HEIGHT - 1,
            rotate90=True,
            physical_width=physical_width,
            physical_height=physical_height,
        )
        self.assertEqual(left_edge, (physical_width - 1, 0))
        self.assertEqual(right_edge, (0, physical_height - 1))

    def test_rotated_copy_uses_dest_pitch_not_mmap_page_rounding(self) -> None:
        plan = test_surface_plan()
        width, height = plan.canvas_width, plan.canvas_height
        src_stride = width * 4
        src = bytearray(src_stride * height)
        white = bytes((255, 255, 255, 255))
        red = bytes((71, 71, 209, 255))
        for stripe in plan.stripes:
            color = bytes((int(stripe.color[2] * 255), int(stripe.color[1] * 255), int(stripe.color[0] * 255), 255))
            for y in range(stripe.height):
                for x in range(stripe.x, stripe.x + stripe.width):
                    offset = y * src_stride + x * 4
                    src[offset:offset + 4] = color

        actual_pitch = 256
        dest = bytearray(actual_pitch * width)
        copy_logical_to_physical_scanout(
            src,
            src_stride=src_stride,
            width=width,
            height=height,
            dest=dest,
            dest_pitch=actual_pitch,
            rotate90=True,
        )
        white_y = plan.stripes[1].x + 10
        offset = white_y * actual_pitch + (height - 1) * 4
        self.assertEqual(bytes(dest[offset:offset + 4]), white)

        wrong_pitch = actual_pitch + 64
        wrong = bytearray(wrong_pitch * width)
        copy_logical_to_physical_scanout(
            src,
            src_stride=src_stride,
            width=width,
            height=height,
            dest=wrong,
            dest_pitch=wrong_pitch,
            rotate90=True,
        )
        self.assertNotEqual(bytes(wrong[offset:offset + 4]), white)
