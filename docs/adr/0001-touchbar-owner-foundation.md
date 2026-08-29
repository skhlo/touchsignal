# Select the react-drm device model as the exclusive Touch Bar owner

TouchSignal needs one reversible owner for the MacBookPro16,1 Touch Bar. The
spike compared `react-drm-for-touchbar` against tiny-dfr and selected the
react-drm device model: USB configuration 2, appletbdrm DRM master, rediscovered
digitizer, backlight, and uinput, with USB configuration 1 as the firmware-row
fallback. tiny-dfr remains the fallback direction if this model cannot be
proved on this machine. Running both is never an option.

## Considered options

- **react-drm-for-touchbar device model, selected.** Upstream already encodes
  attach, detach, Hyprland-safe seat assignment, logind delay-inhibit suspend
  handling, and firmware-row restore. The GPL-3.0-or-later control center is
  not vendored. TouchSignal keeps a small owner that follows the same
  USB/DRM/input contract. This spike implements attach, exclusive lock, a
  2008 by 60 test surface, digitizer discovery, and USB configuration 1
  restore. Optional seat udev is uninstalled. Logind inhibit is owned by
  Omarchy, not by this owner.
- **tiny-dfr device model, rejected as the first owner.** It is a mature static
  DRM renderer and a proven fallback, but it has no documented logind resume
  contract and its configuration is a mapped function row, not a workflow HUD.
  If the react-drm attach/resume path cannot be proved, rebuild the owner around
  tiny-dfr's DRM/libinput/uinput model instead of installing tiny-dfr beside
  TouchSignal.
- **Installing the react-drm control center or tiny-dfr service, rejected.**
  Either daemon would become a second owner and would replace the kernel
  firmware row before the spike had a tested restore path.

## Ownership boundary

Adapters never receive DRM, touch, backlight, or uinput handles. The owner is
the only module allowed to switch USB 05ac:8302, open the appletbdrm card, or
restore configuration 1. This spike opens display, touch, backlight, and
uinput. Intel and AMD DRM cards stay untouched.

## Measurements

Automated tests cover exclusive claim, competing-renderer refusal, failed
attach restore, 2008 by 60 presentation, and touch-down/move/up reporting
against a fake host.

Live attach on this MacBookPro16,1 switched USB 05ac:8302 to configuration 2
and created appletbdrm `/dev/dri/card0`. The only connected DRM mode was
60 by 2008, presented as logical 2008 by 60. That measured MacBookPro16,1
mode is the native test surface. It is not a generic Touch Bar size.

Live proof recorded on 2026-08-29:

- Exclusive claim of display, touch, backlight, and virtual input succeeded.
- Physical touch reported two complete down/move/up sequences.
- Visible scanout showed alternating red and white stripes across the bar.
- Five idle minutes measured 0.0244 percent of one core, then restored USB
  configuration 1.
- Suspend/resume cycle 1, with the firmware row already restored, returned
  USB configuration 1.
- Cycles 2 and 3 held the owner through suspend. After resume, appletbdrm
  reappeared as a new DRM card. Release failed with no such device, and
  restore returned USB configuration 1, special-key mode 2, Fn Y, autodim Y,
  and brightness 2. That is the restore-on-reattach-failure path.
