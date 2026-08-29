# TouchSignal

TouchSignal is an Omarchy-aware Touch Bar HUD for agent workflows on an Intel
MacBookPro16,1. It is a supplemental physical interface, not a replacement for
Herdr, Hyprland, Codex, or the kernel firmware row.

## Language

**Touch Bar owner**:
The only process allowed to open the Touch Bar DRM card, digitizer, backlight,
or virtual-input device.
_Avoid_: renderer daemon, panel server, compositor client

**Firmware row**:
The kernel special-key strip shown when USB 05ac:8302 stays in configuration 1
and hid_appletb_kbd owns the bar.
_Avoid_: tiny-dfr fallback, stock Touch Bar, function row

**Digitizer**:
The Touch Bar touch device that appears after appletbdrm attaches. It is not
the firmware keyboard named Apple Inc. Touch Bar Display.
_Avoid_: event7, touchpad, firmware keyboard

**Native test surface**:
The exclusive owner's 2170 by 60 pixel scanout used to prove display ownership.
_Avoid_: preview canvas, logical 2008x60 fallback
