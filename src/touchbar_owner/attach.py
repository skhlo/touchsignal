from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from time import monotonic, sleep as default_sleep

from .discovery import appletbdrm_card, find_touchbar_usb
from .drm import inspect_appletbdrm_card
from .types import DRM_CONFIG, DrmCard


def _write_sysfs(path: Path, value: str) -> None:
    path.write_text(value if value.endswith("\n") else value + "\n")


def attach_touchbar(
    root: Path | None = None,
    sleep: Callable[[float], None] = default_sleep,
    timeout: float = 20.0,
    inspect: Callable[[str], DrmCard] = inspect_appletbdrm_card,
) -> DrmCard:
    deadline = monotonic() + timeout
    usb = find_touchbar_usb(root)
    while usb is None:
        if monotonic() > deadline:
            raise RuntimeError("Touch Bar USB device 05ac:8302 did not enumerate")
        sleep(0.25)
        usb = find_touchbar_usb(root)

    config_path = usb / "bConfigurationValue"
    while True:
        current = config_path.read_text(encoding="utf-8", errors="replace").strip()
        if current == DRM_CONFIG:
            break
        try:
            _write_sysfs(config_path, "0")
            _write_sysfs(config_path, DRM_CONFIG)
        except OSError as exc:
            if monotonic() > deadline:
                raise RuntimeError(f"unable to switch Touch Bar to config {DRM_CONFIG}: {exc}") from exc
            sleep(0.5)
            usb = find_touchbar_usb(root) or usb
            config_path = usb / "bConfigurationValue"
            continue
        usb = find_touchbar_usb(root) or usb
        config_path = usb / "bConfigurationValue"
        current = config_path.read_text(encoding="utf-8", errors="replace").strip()
        if current == DRM_CONFIG:
            break
        if monotonic() > deadline:
            raise RuntimeError(f"unable to switch Touch Bar to config {DRM_CONFIG}")
        sleep(0.25)

    last_error: Exception | None = None
    while True:
        card = appletbdrm_card(root)
        if card is not None:
            try:
                return inspect(card.path)
            except Exception as exc:
                last_error = exc
        if monotonic() > deadline:
            if last_error is not None:
                raise RuntimeError(str(last_error)) from last_error
            raise RuntimeError("appletbdrm card did not appear")
        sleep(0.25)
