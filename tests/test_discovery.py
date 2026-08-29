from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from touchbar_owner.discovery import (
    competing_renderer_names,
    find_touchbar_usb,
    list_drm_cards,
    parse_input_devices,
    read_firmware_row,
)


INPUT_DEVICES = """\
I: Bus=0003 Vendor=05ac Product=8302 Version=0101
N: Name="Apple Inc. Touch Bar Display"
P: Phys=usb-t2bce_vhci-6/input0
S: Sysfs=/devices/pci0000:00/0000:00:1b.0/0000:04:00.1/t2bce_core/t2bce_core/t2bce_vhci/usb7/7-6/7-6:1.0/0003:05AC:8302.0006/input/input7
U: Uniq=0000000000000000
H: Handlers=kbd event7
B: PROP=0
B: EV=100013

I: Bus=0003 Vendor=05ac Product=8302 Version=0101
N: Name="Apple Inc. Touch Bar Display Touchpad"
P: Phys=usb-t2bce_vhci-6/input1
S: Sysfs=/devices/example/input/input20
U: Uniq=
H: Handlers=event20
B: PROP=2
B: EV=b
"""


class DiscoveryTests(unittest.TestCase):
    def test_firmware_keyboard_is_not_a_digitizer(self) -> None:
        devices = parse_input_devices(INPUT_DEVICES)
        firmware = next(device for device in devices if device.path.endswith("event7"))
        digitizer = next(device for device in devices if device.path.endswith("event20"))
        self.assertTrue(firmware.is_firmware_keyboard)
        self.assertFalse(firmware.is_touchbar_digitizer)
        self.assertTrue(digitizer.is_touchbar_digitizer)

    def test_finds_only_the_appletbdrm_card(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            drm = root / "sys/class/drm"
            self._write_card(drm, "card1", "i915")
            self._write_card(drm, "card2", "amdgpu")
            self._write_card(drm, "card3", "appletbdrm")
            cards = list_drm_cards(root)
            self.assertEqual([card.driver for card in cards], ["i915", "amdgpu", "appletbdrm"])
            apple = next(card for card in cards if card.driver == "appletbdrm")
            self.assertEqual(apple.path, "/dev/dri/card3")

    def test_finds_the_touch_bar_usb_device(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            usb = root / "sys/bus/usb/devices/7-6"
            usb.mkdir(parents=True)
            (usb / "idVendor").write_text("05ac\n")
            (usb / "idProduct").write_text("8302\n")
            (usb / "bConfigurationValue").write_text("1\n")
            found = find_touchbar_usb(root)
            self.assertEqual(found, usb)

    def test_firmware_row_matches_the_observed_baseline_shape(self) -> None:
        with TemporaryDirectory() as raw:
            root = self._write_firmware_sysfs(Path(raw), config="1", loaded=False)
            row = read_firmware_row(root)
            self.assertEqual(row.usb_configuration, "1")
            self.assertEqual(row.special_key_mode, "2")
            self.assertEqual(row.fn_toggle, "Y")
            self.assertEqual(row.autodim, "Y")
            self.assertEqual(row.brightness, "2")
            self.assertFalse(row.appletbdrm_loaded)

    def test_competing_renderer_scan_ignores_the_spike_owner(self) -> None:
        with TemporaryDirectory() as raw:
            proc = Path(raw) / "proc"
            self._write_proc(proc, "100", "tiny-dfr", "/usr/bin/tiny-dfr")
            self._write_proc(
                proc,
                "101",
                "python3",
                "python3 -m touchbar_owner run",
            )
            names = competing_renderer_names(Path(raw))
            self.assertEqual(names, ("tiny-dfr",))

    def _write_card(self, drm: Path, name: str, driver: str) -> None:
        card = drm / name
        card.mkdir(parents=True)
        (card / "device").mkdir()
        (card / "device" / "uevent").write_text(f"DRIVER={driver}\n")

    def _write_proc(self, proc: Path, pid: str, comm: str, cmdline: str) -> None:
        directory = proc / pid
        directory.mkdir(parents=True)
        (directory / "comm").write_text(comm + "\n")
        (directory / "cmdline").write_text(cmdline.replace(" ", "\0") + "\0")

    def _write_firmware_sysfs(self, root: Path, config: str, loaded: bool) -> Path:
        usb = root / "sys/bus/usb/devices/7-6"
        usb.mkdir(parents=True)
        (usb / "idVendor").write_text("05ac\n")
        (usb / "idProduct").write_text("8302\n")
        (usb / "bConfigurationValue").write_text(config + "\n")
        kbd = root / "sys/module/hid_appletb_kbd/parameters"
        kbd.mkdir(parents=True)
        (kbd / "mode").write_text("2\n")
        (kbd / "fntoggle").write_text("Y\n")
        (kbd / "autodim").write_text("Y\n")
        bl_mod = root / "sys/module/hid_appletb_bl/parameters"
        bl_mod.mkdir(parents=True)
        (bl_mod / "brightness").write_text("2\n")
        bl = root / "sys/class/backlight/appletb_backlight"
        bl.mkdir(parents=True)
        (bl / "brightness").write_text("2\n")
        (bl / "actual_brightness").write_text("2\n")
        modules = root / "proc"
        modules.mkdir(parents=True)
        if loaded:
            (modules / "modules").write_text("appletbdrm 1 0 Live 0x0\n")
        else:
            (modules / "modules").write_text("hid_appletb_kbd 1 0 Live 0x0\n")
        return root
