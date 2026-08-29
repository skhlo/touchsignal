from __future__ import annotations

import unittest

from touchbar_owner.types import OBSERVED_BASELINE, FirmwareRow, firmware_row_restored


class FirmwareRowTests(unittest.TestCase):
    def test_autodim_zero_brightness_still_counts_as_restored(self) -> None:
        dimmed = FirmwareRow(
            usb_configuration="1",
            special_key_mode="2",
            fn_toggle="Y",
            autodim="Y",
            brightness="0",
            appletbdrm_loaded=False,
        )
        self.assertTrue(firmware_row_restored(dimmed, OBSERVED_BASELINE))

    def test_config_two_is_not_restored(self) -> None:
        attached = FirmwareRow(
            usb_configuration="2",
            special_key_mode="2",
            fn_toggle="Y",
            autodim="Y",
            brightness="2",
            appletbdrm_loaded=True,
        )
        self.assertFalse(firmware_row_restored(attached, OBSERVED_BASELINE))
