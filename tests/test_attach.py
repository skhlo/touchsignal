from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from touchbar_owner.attach import attach_touchbar
from touchbar_owner.types import DRM_CONFIG
from touchbar_owner.types import DrmCard, NATIVE_HEIGHT, NATIVE_WIDTH


class AttachTests(unittest.TestCase):
    def test_attach_writes_config_two_and_waits_for_appletbdrm(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            usb = root / "sys/bus/usb/devices/7-6"
            usb.mkdir(parents=True)
            (usb / "idVendor").write_text("05ac\n")
            (usb / "idProduct").write_text("8302\n")
            (usb / "bConfigurationValue").write_text("1\n")
            drm = root / "sys/class/drm"
            calls = {"n": 0}

            def sleep(_seconds: float) -> None:
                calls["n"] += 1
                if calls["n"] == 1:
                    card = drm / "card3"
                    card.mkdir(parents=True)
                    (card / "device").mkdir()
                    (card / "device" / "uevent").write_text("DRIVER=appletbdrm\n")

            def fake_inspect(path: str) -> DrmCard:
                return DrmCard("card3", "appletbdrm", path, NATIVE_HEIGHT, NATIVE_WIDTH, True)

            card = attach_touchbar(root, sleep=sleep, timeout=2.0, inspect=fake_inspect)
            self.assertEqual(card.driver, "appletbdrm")
            self.assertEqual((usb / "bConfigurationValue").read_text().strip(), DRM_CONFIG)


    def test_attach_retries_until_the_connector_is_connected(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            usb = root / "sys/bus/usb/devices/7-6"
            usb.mkdir(parents=True)
            (usb / "idVendor").write_text("05ac\n")
            (usb / "idProduct").write_text("8302\n")
            (usb / "bConfigurationValue").write_text("1\n")
            drm = root / "sys/class/drm"
            calls = {"n": 0}
            inspect_calls = {"n": 0}

            def sleep(_seconds: float) -> None:
                calls["n"] += 1
                if calls["n"] == 1:
                    card = drm / "card0"
                    card.mkdir(parents=True)
                    (card / "device").mkdir()
                    (card / "device" / "uevent").write_text("DRIVER=appletbdrm\n")

            def fake_inspect(path: str) -> DrmCard:
                inspect_calls["n"] += 1
                if inspect_calls["n"] == 1:
                    raise RuntimeError(f"no connected Touch Bar connector on {path}")
                return DrmCard("card0", "appletbdrm", path, NATIVE_HEIGHT, NATIVE_WIDTH, True)

            card = attach_touchbar(root, sleep=sleep, timeout=2.0, inspect=fake_inspect)
            self.assertEqual(card.driver, "appletbdrm")
            self.assertEqual(inspect_calls["n"], 2)
    def test_attach_times_out_without_an_appletbdrm_card(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            usb = root / "sys/bus/usb/devices/7-6"
            usb.mkdir(parents=True)
            (usb / "idVendor").write_text("05ac\n")
            (usb / "idProduct").write_text("8302\n")
            (usb / "bConfigurationValue").write_text("1\n")
            with self.assertRaisesRegex(RuntimeError, "appletbdrm"):
                attach_touchbar(root, sleep=lambda _seconds: None, timeout=0.0)
