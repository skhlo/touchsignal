# Select the react-drm device model as the exclusive Touch Bar owner

TouchSignal needs one reversible owner for the MacBookPro16,1 Touch Bar. The
spike compared `react-drm-for-touchbar` against tiny-dfr and selected the
react-drm device model: USB configuration 2, appletbdrm DRM master, rediscovered
digitizer, and backlight, with USB configuration 1 as the firmware-row
fallback. uinput remains part of the later Fn layer, not of this spike's
claim set. tiny-dfr remains the fallback direction if this model cannot be
proved on this machine. Running both is never an option.

## Considered options

- **react-drm-for-touchbar device model, selected.** Upstream already encodes
  attach, detach, Hyprland-safe seat assignment, logind delay-inhibit suspend
  handling, and firmware-row restore. The GPL-3.0-or-later control center is
  not vendored. TouchSignal keeps a small owner that follows the same
  USB/DRM/input contract. This spike implements attach, exclusive lock, a
  2170 by 60 test surface, digitizer discovery, and USB configuration 1
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
restore configuration 1. This spike opens display, touch, and backlight.
uinput stays closed until the Fn layer injects keys. Intel and AMD DRM cards
stay untouched.

## Measurements

Automated tests cover exclusive claim, competing-renderer refusal, failed
attach restore, 2170 by 60 presentation, and touch-down/move/up reporting
against a fake host. Live attach, physical touch, idle CPU, and three
suspend/resume cycles remain machine evidence and must be recorded from the
MacBookPro16,1 before this decision is treated as hardware-complete.
