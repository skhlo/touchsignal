# Touch Bar owner spike

This spike records one exclusive renderer foundation for MacBookPro16,1 and
ships reversible attach/restore tooling. It is not the TouchSignal 0.1
runtime and it has not completed live attach.

## Observed baseline

Recorded on 2026-08-29 before any owner change:

| Surface | Value |
| --- | --- |
| Product | MacBookPro16,1 |
| Kernel | 7.1.8-arch1-Watanare-T2-3-t2 |
| USB 05ac:8302 configuration | 1 |
| hid_appletb_kbd mode | 2 |
| Fn toggle | Y |
| autodim | Y |
| appletb_backlight brightness | 2 |
| appletbdrm | installed, unloaded |
| Live brightness at later status | 0 while autodim remained Y |
| Visible DRM cards | i915 card1, amdgpu card2 |
| Firmware input | Apple Inc. Touch Bar Display on event7 |
| Competing renderer | none |

The firmware keyboard on event7 is not a digitizer. After a successful attach
the owner must rediscover the Touch Bar touch device instead of caching event7.

A later read-only status check still showed USB configuration 1, special-key
mode 2, Fn toggle Y, and autodim Y. Actual backlight had fallen to 0 because
autodim was active. Restore therefore treats brightness 0, 1, or 2 as valid
when autodim remains enabled.

The same status check showed no competing renderer and these access limits:

| Resource | Writable by the session user |
| --- | --- |
| USB `bConfigurationValue` | no |
| appletb_backlight brightness | no |
| `video` group | no |
| `input` group | yes |

Live attach therefore needs one approved privileged path before the owner may
switch USB configuration 2 or open appletbdrm. The Intel and AMD DRM cards were
not opened.

## Recovery command

Keep this command ready before claiming the bar:

```bash
PYTHONPATH=src python3 -m touchbar_owner.cli restore
```

The wrapper is:

```bash
python3 scripts/restore-touchbar-firmware-row
```

The restore path switches USB 05ac:8302 back to configuration 1, which unbinds
the appletbdrm display interface, then restores special-key mode 2, Fn
toggling, automatic dimming, and brightness 2. A failed write is an error.
Brightness 0 still counts as restored when autodim remains enabled. Restore
never modesets i915 or amdgpu and never loads or unloads kernel modules.

If the configuration sysfs node is not writable, restore needs one privileged
write to `bConfigurationValue`. Ask before that write. Do not load or unload
kernel modules without asking.

## Exclusive owner

The selected foundation is the react-drm-for-touchbar device model:

1. Refuse tiny-dfr, react-drm, mac-touchbar-plus, or a second TouchSignal owner.
2. Take one flock on `/tmp/touchsignal-touchbar-owner.lock`.
3. Switch USB 05ac:8302 from configuration 1 to configuration 2.
4. Wait for the appletbdrm DRM card and open only that card.
5. Rediscover the Touch Bar digitizer.
6. Open backlight and uinput.
7. Present a stable 2170 by 60 test surface.
8. Restore the firmware row on normal stop or failed attach.

Never run two Touch Bar renderers. Never open card1 or card2.

## Commands

Status, without changing hardware:

```bash
PYTHONPATH=src python3 -m touchbar_owner.cli status
```

Claim, draw the test surface, report touch, then restore:

```bash
PYTHONPATH=src python3 -m touchbar_owner.cli claim --seconds 30 --require-touch
```

Idle measurement:

```bash
PYTHONPATH=src python3 -m touchbar_owner.cli idle --seconds 300
```

## Reversal

1. Stop any `touchbar_owner` claim process.
2. Run `PYTHONPATH=src python3 -m touchbar_owner.cli restore`.
3. Confirm USB configuration 1, special-key mode 2, Fn toggle Y, autodim Y,
   brightness 2 or autodim brightness 0, and no appletbdrm DRM card.
4. If `udev/99-touchsignal-touchbar-owner.rules` was installed, remove it,
   reload udev, and retrigger USB and backlight devices.
5. Do not add the user to `video`. This spike uses the existing `input` group.

## Privileged changes

Ask before:

- installing packages;
- changing systemd units or other persistent configuration;
- installing or removing udev rules;
- adding the user to `video` or `input`;
- writing USB `bConfigurationValue`;
- loading or unloading `appletbdrm`.

Do not edit `/usr/share/omarchy/`.
Do not edit `~/.config/hypr/monitors.lua` from this repository. If a later
live attach shows a Touch Bar connector in `hyprctl monitors all`, disable
that named output locally after confirming it, then run `hyprctl reload` and
`hyprctl configerrors`. `eDP-2` is the apple_gmux dummy, not the Touch Bar.
