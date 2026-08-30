from __future__ import annotations

from pathlib import Path

from .types import (
    COMPETING_RENDERERS,
    USB_PRODUCT,
    USB_VENDOR,
    DrmCard,
    FirmwareRow,
    InputDevice,
)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").strip()


def parse_input_devices(text: str) -> list[InputDevice]:
    devices: list[InputDevice] = []
    for block in text.strip().split("\n\n"):
        name = ""
        handler = ""
        bus = ""
        vendor = ""
        product = ""
        sysfs = ""
        key_codes: frozenset[int] = frozenset()
        for line in block.splitlines():
            if line.startswith("I: "):
                identity = {}
                for token in line[3:].split():
                    if "=" in token:
                        key, value = token.split("=", 1)
                        identity[key.casefold()] = value
                bus = identity.get("bus", "")
                vendor = identity.get("vendor", "")
                product = identity.get("product", "")
            elif line.startswith("N: Name="):
                name = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("S: Sysfs="):
                sysfs = line.split("=", 1)[1].strip()
            elif line.startswith("H: Handlers="):
                for token in line.split("=", 1)[1].split():
                    if token.startswith("event"):
                        handler = token
                        break
            elif line.startswith("B: KEY="):
                key_codes = _parse_key_bitmap(line.split("=", 1)[1])
        if name and handler:
            devices.append(
                InputDevice(
                    name=name,
                    path=f"/dev/input/{handler}",
                    bus=bus,
                    vendor=vendor,
                    product=product,
                    sysfs=sysfs,
                    key_codes=key_codes,
                )
            )
    return devices


def _parse_key_bitmap(raw: str) -> frozenset[int]:
    codes: set[int] = set()
    for word_index, token in enumerate(reversed(raw.split())):
        try:
            word = int(token, 16)
        except ValueError:
            return frozenset()
        while word:
            bit = (word & -word).bit_length() - 1
            codes.add(word_index * 64 + bit)
            word &= word - 1
    return frozenset(codes)


def list_input_devices(root: Path | None = None) -> list[InputDevice]:
    path = (root / "proc/bus/input/devices") if root else Path("/proc/bus/input/devices")
    return parse_input_devices(path.read_text(encoding="utf-8", errors="replace"))


def find_touchbar_usb(root: Path | None = None) -> Path | None:
    bus = (root / "sys/bus/usb/devices") if root else Path("/sys/bus/usb/devices")
    if not bus.exists():
        return None
    for entry in sorted(bus.iterdir()):
        vendor = entry / "idVendor"
        product = entry / "idProduct"
        if not vendor.exists() or not product.exists():
            continue
        if _read_text(vendor) == USB_VENDOR and _read_text(product) == USB_PRODUCT:
            return entry
    return None


def list_drm_cards(root: Path | None = None) -> list[DrmCard]:
    drm = (root / "sys/class/drm") if root else Path("/sys/class/drm")
    cards: list[DrmCard] = []
    if not drm.exists():
        return cards
    for entry in sorted(drm.iterdir()):
        if not entry.name.startswith("card") or "-" in entry.name:
            continue
        uevent = entry / "device" / "uevent"
        if not uevent.exists():
            continue
        driver = ""
        for line in _read_text(uevent).splitlines():
            if line.startswith("DRIVER="):
                driver = line.split("=", 1)[1].strip()
                break
        cards.append(
            DrmCard(
                name=entry.name,
                driver=driver,
                path=f"/dev/dri/{entry.name}",
                hdisplay=0,
                vdisplay=0,
                rotate90=False,
            )
        )
    return cards


def appletbdrm_card(root: Path | None = None) -> DrmCard | None:
    for card in list_drm_cards(root):
        if card.driver == "appletbdrm":
            return card
    return None


def appletbdrm_loaded(root: Path | None = None) -> bool:
    return appletbdrm_card(root) is not None


def _param(root: Path | None, module: str, name: str, default: str = "") -> str:
    path = (
        (root / f"sys/module/{module}/parameters/{name}")
        if root
        else Path(f"/sys/module/{module}/parameters/{name}")
    )
    if not path.exists():
        return default
    return _read_text(path)


def _brightness(root: Path | None) -> str:
    bl = (
        (root / "sys/class/backlight/appletb_backlight")
        if root
        else Path("/sys/class/backlight/appletb_backlight")
    )
    for name in ("actual_brightness", "brightness"):
        path = bl / name
        if path.exists():
            return _read_text(path)
    return _param(root, "hid_appletb_bl", "brightness", "")


def read_firmware_row(root: Path | None = None) -> FirmwareRow:
    usb = find_touchbar_usb(root)
    configuration = _read_text(usb / "bConfigurationValue") if usb else ""
    return FirmwareRow(
        usb_configuration=configuration,
        special_key_mode=_param(root, "hid_appletb_kbd", "mode"),
        fn_toggle=_param(root, "hid_appletb_kbd", "fntoggle"),
        autodim=_param(root, "hid_appletb_kbd", "autodim"),
        brightness=_brightness(root),
        appletbdrm_loaded=appletbdrm_loaded(root),
    )


def competing_renderer_names(root: Path | None = None) -> tuple[str, ...]:
    proc = (root / "proc") if root else Path("/proc")
    found: list[str] = []
    if not proc.exists():
        return ()
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        comm_path = entry / "comm"
        cmd_path = entry / "cmdline"
        comm = _read_text(comm_path) if comm_path.exists() else ""
        cmdline = ""
        if cmd_path.exists():
            cmdline = cmd_path.read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        blob = f"{comm} {cmdline}"
        if "touchbar_owner" in blob or "touchsignal-touchbar-owner" in blob:
            continue
        for marker in COMPETING_RENDERERS:
            if marker in blob:
                found.append(marker)
                break
    return tuple(dict.fromkeys(found))
