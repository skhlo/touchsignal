from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from touchbar_owner.restore import restore_firmware_row
from touchbar_owner.types import OBSERVED_BASELINE


class RestoreTests(unittest.TestCase):
    def test_restore_returns_usb_config_one_and_drops_the_appletbdrm_card(self) -> None:
        with TemporaryDirectory() as raw:
            root = self._write_attached_sysfs(Path(raw))
            row = restore_firmware_row(root)
            usb = root / "sys/bus/usb/devices/7-6/bConfigurationValue"
            self.assertEqual(usb.read_text().strip(), "1")
            self.assertEqual(row.usb_configuration, "1")
            self.assertFalse(row.appletbdrm_loaded)
            self.assertFalse((root / "sys/class/drm/card3").exists())
            brightness = root / "sys/class/backlight/appletb_backlight/brightness"
            self.assertEqual(brightness.read_text().strip(), "2")
            self.assertEqual(row, OBSERVED_BASELINE)

    def test_restore_is_safe_when_already_on_the_firmware_row(self) -> None:
        with TemporaryDirectory() as raw:
            root = self._write_attached_sysfs(Path(raw), config="1", brightness="2")
            row = restore_firmware_row(root)
            self.assertEqual(row, OBSERVED_BASELINE)

    def test_restore_does_not_write_read_only_mode(self) -> None:
        with TemporaryDirectory() as raw:
            root = self._write_attached_sysfs(Path(raw), mode="2")
            mode = root / "sys/module/hid_appletb_kbd/parameters/mode"
            mode.chmod(0o444)
            original_write = Path.write_text
            written: list[str] = []

            def track(self: Path, data: str, *args: object, **kwargs: object) -> int:
                written.append(str(self))
                return original_write(self, data, *args, **kwargs)

            Path.write_text = track  # type: ignore[method-assign]
            try:
                row = restore_firmware_row(root)
            finally:
                Path.write_text = original_write  # type: ignore[method-assign]
            self.assertEqual(row.usb_configuration, "1")
            self.assertFalse(any(path.endswith("/parameters/mode") for path in written))
            self.assertEqual(mode.read_text().strip(), "2")

    def test_restore_fails_when_fn_toggle_cannot_be_written(self) -> None:
        with TemporaryDirectory() as raw:
            root = self._write_attached_sysfs(Path(raw))
            blocked = {root / "sys/module/hid_appletb_kbd/parameters/fntoggle"}
            original_write = Path.write_text

            def refuse(self: Path, data: str, *args: object, **kwargs: object) -> int:
                if self in blocked:
                    raise OSError(13, "permission denied")
                return original_write(self, data, *args, **kwargs)

            Path.write_text = refuse  # type: ignore[method-assign]
            try:
                with self.assertRaisesRegex(RuntimeError, "Fn toggle"):
                    restore_firmware_row(root)
            finally:
                Path.write_text = original_write  # type: ignore[method-assign]

    def test_restore_fails_when_autodim_cannot_be_written(self) -> None:
        with TemporaryDirectory() as raw:
            root = self._write_attached_sysfs(Path(raw))
            blocked = {root / "sys/module/hid_appletb_kbd/parameters/autodim"}
            original_write = Path.write_text

            def refuse(self: Path, data: str, *args: object, **kwargs: object) -> int:
                if self in blocked:
                    raise OSError(13, "permission denied")
                return original_write(self, data, *args, **kwargs)

            Path.write_text = refuse  # type: ignore[method-assign]
            try:
                with self.assertRaisesRegex(RuntimeError, "autodim"):
                    restore_firmware_row(root)
            finally:
                Path.write_text = original_write  # type: ignore[method-assign]

    def test_restore_fails_when_brightness_cannot_be_written(self) -> None:
        with TemporaryDirectory() as raw:
            root = self._write_attached_sysfs(Path(raw), brightness="1")
            blocked = {root / "sys/class/backlight/appletb_backlight/brightness"}
            original_write = Path.write_text

            def refuse(self: Path, data: str, *args: object, **kwargs: object) -> int:
                if self in blocked:
                    raise OSError(13, "permission denied")
                return original_write(self, data, *args, **kwargs)

            Path.write_text = refuse  # type: ignore[method-assign]
            try:
                with self.assertRaisesRegex(RuntimeError, "brightness"):
                    restore_firmware_row(root)
            finally:
                Path.write_text = original_write  # type: ignore[method-assign]

    def test_restore_fails_when_special_key_mode_stays_wrong(self) -> None:
        with TemporaryDirectory() as raw:
            root = self._write_attached_sysfs(Path(raw), mode="1")
            mode = root / "sys/module/hid_appletb_kbd/parameters/mode"
            mode.chmod(0o444)
            with self.assertRaisesRegex(RuntimeError, "special-key mode"):
                restore_firmware_row(root)

    def test_restore_fails_when_appletbdrm_card_remains(self) -> None:
        with TemporaryDirectory() as raw:
            root = self._write_attached_sysfs(Path(raw))
            original = restore_firmware_row.__globals__["shutil"].rmtree

            def keep_card(path, ignore_errors=False):
                return None

            restore_firmware_row.__globals__["shutil"].rmtree = keep_card
            try:
                with self.assertRaisesRegex(RuntimeError, "appletbdrm"):
                    restore_firmware_row(root)
            finally:
                restore_firmware_row.__globals__["shutil"].rmtree = original

    def test_restore_fails_if_usb_configuration_stays_two(self) -> None:
        with TemporaryDirectory() as raw:
            root = self._write_attached_sysfs(Path(raw), config="2")
            target = root / "sys/bus/usb/devices/7-6/bConfigurationValue"
            original_write = Path.write_text

            def stuck(self: Path, data: str, *args: object, **kwargs: object) -> int:
                if self == target:
                    raise OSError(13, "permission denied")
                return original_write(self, data, *args, **kwargs)

            Path.write_text = stuck  # type: ignore[method-assign]
            try:
                with self.assertRaisesRegex(RuntimeError, "USB configuration"):
                    restore_firmware_row(root, sleep=lambda _seconds: None)
            finally:
                Path.write_text = original_write  # type: ignore[method-assign]
            self.assertEqual(target.read_text().strip(), "2")

    def test_restore_retries_a_failed_config_write(self) -> None:
        with TemporaryDirectory() as raw:
            root = self._write_attached_sysfs(Path(raw), config="2")
            target = root / "sys/bus/usb/devices/7-6/bConfigurationValue"
            attempts = {"count": 0}
            original_write = Path.write_text

            def flaky(self: Path, data: str, *args: object, **kwargs: object) -> int:
                if self == target and data.strip() in {"0", "1"}:
                    attempts["count"] += 1
                    if attempts["count"] < 3:
                        raise OSError(110, "timed out")
                return original_write(self, data, *args, **kwargs)

            Path.write_text = flaky  # type: ignore[method-assign]
            try:
                row = restore_firmware_row(root, sleep=lambda _seconds: None)
            finally:
                Path.write_text = original_write  # type: ignore[method-assign]
            self.assertEqual(row.usb_configuration, "1")
            self.assertGreaterEqual(attempts["count"], 3)

    def _write_attached_sysfs(
        self,
        root: Path,
        config: str = "2",
        brightness: str = "1",
        mode: str = "2",
    ) -> Path:
        usb = root / "sys/bus/usb/devices/7-6"
        usb.mkdir(parents=True)
        (usb / "idVendor").write_text("05ac\n")
        (usb / "idProduct").write_text("8302\n")
        (usb / "bConfigurationValue").write_text(config + "\n")
        kbd = root / "sys/module/hid_appletb_kbd/parameters"
        kbd.mkdir(parents=True)
        (kbd / "mode").write_text(mode + "\n")
        (kbd / "fntoggle").write_text("N\n")
        (kbd / "autodim").write_text("N\n")
        bl_mod = root / "sys/module/hid_appletb_bl/parameters"
        bl_mod.mkdir(parents=True)
        (bl_mod / "brightness").write_text("1\n")
        bl = root / "sys/class/backlight/appletb_backlight"
        bl.mkdir(parents=True)
        (bl / "brightness").write_text(brightness + "\n")
        (bl / "actual_brightness").write_text(brightness + "\n")
        proc = root / "proc"
        proc.mkdir(parents=True)
        (proc / "modules").write_text("appletbdrm 1 0 Live 0x0\n")
        if config == "2":
            card = root / "sys/class/drm/card3"
            card.mkdir(parents=True)
            (card / "device").mkdir()
            (card / "device" / "uevent").write_text("DRIVER=appletbdrm\n")
        return root
