from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from time import sleep as default_sleep

from .discovery import appletbdrm_card, find_touchbar_usb, read_firmware_row
from .types import FIRMWARE_CONFIG, OBSERVED_BASELINE, FirmwareRow


def _write_sysfs(path: Path, value: str) -> None:
    path.write_text(value if value.endswith("\n") else value + "\n")


def _set_if_different(path: Path, desired: str) -> None:
    if not path.exists():
        return
    current = path.read_text(encoding="utf-8", errors="replace").strip()
    if current == desired:
        return
    try:
        _write_sysfs(path, desired)
    except OSError:
        return


def _switch_usb_to_firmware(usb: Path, sleep: Callable[[float], None], attempts: int = 10) -> None:
    config_path = usb / "bConfigurationValue"
    for _ in range(attempts):
        current = config_path.read_text(encoding="utf-8", errors="replace").strip()
        if current == FIRMWARE_CONFIG:
            return
        try:
            _write_sysfs(config_path, "0")
            _write_sysfs(config_path, FIRMWARE_CONFIG)
        except OSError:
            sleep(0.05)
            continue
        current = config_path.read_text(encoding="utf-8", errors="replace").strip()
        if current == FIRMWARE_CONFIG:
            return
        sleep(0.05)
    current = config_path.read_text(encoding="utf-8", errors="replace").strip()
    if current != FIRMWARE_CONFIG:
        raise RuntimeError(
            f"Touch Bar USB configuration is {current or 'unknown'}, expected {FIRMWARE_CONFIG}"
        )


def restore_firmware_row(
    root: Path | None = None,
    sleep: Callable[[float], None] = default_sleep,
) -> FirmwareRow:
    usb = find_touchbar_usb(root)
    if usb is None:
        raise RuntimeError("Touch Bar USB device 05ac:8302 was not found")
    _switch_usb_to_firmware(usb, sleep=sleep)
    if root is not None:
        # Fake sysfs trees do not unbind drivers. Live restore relies on the kernel.
        card = appletbdrm_card(root)
        if card is not None:
            shutil.rmtree(root / "sys/class/drm" / Path(card.path).name, ignore_errors=True)

    module_root = (root / "sys/module") if root else Path("/sys/module")
    backlight = (
        (root / "sys/class/backlight/appletb_backlight/brightness")
        if root
        else Path("/sys/class/backlight/appletb_backlight/brightness")
    )
    _set_if_different(module_root / "hid_appletb_kbd/parameters/fntoggle", OBSERVED_BASELINE.fn_toggle)
    _set_if_different(module_root / "hid_appletb_kbd/parameters/autodim", OBSERVED_BASELINE.autodim)
    _set_if_different(backlight, OBSERVED_BASELINE.brightness)

    row = read_firmware_row(root)
    if row.usb_configuration != FIRMWARE_CONFIG:
        raise RuntimeError("firmware USB configuration was not restored")
    if appletbdrm_card(root) is not None:
        raise RuntimeError("appletbdrm card remained after USB configuration 1")
    return row
