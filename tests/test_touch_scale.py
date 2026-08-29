from __future__ import annotations

import unittest

from touchbar_owner.input import scale_touch
from touchbar_owner.types import NATIVE_HEIGHT, NATIVE_WIDTH, TOUCH_MAX_X


class TouchScaleTests(unittest.TestCase):
    def test_zero_raw_axis_maps_to_the_origin(self) -> None:
        self.assertEqual(scale_touch(0, 0), (0, 0))

    def test_max_raw_axis_maps_to_native_width(self) -> None:
        x, y = scale_touch(TOUCH_MAX_X, 0)
        self.assertEqual(x, NATIVE_WIDTH - 1)
        self.assertEqual(y, 0)

    def test_mid_bar_stays_inside_the_native_surface(self) -> None:
        x, y = scale_touch(TOUCH_MAX_X // 2, 64)
        self.assertGreater(x, 0)
        self.assertLess(x, NATIVE_WIDTH)
        self.assertGreaterEqual(y, 0)
        self.assertLess(y, NATIVE_HEIGHT)
