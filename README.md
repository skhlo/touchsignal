# TouchSignal

TouchSignal is an Omarchy-aware Touch Bar HUD for agent workflows on an Intel
MacBook Pro 16,1.

It turns the otherwise static Touch Bar into a small, direct status and focus
surface for the Codex app and Herdr workspaces while preserving the machine's
Fn and media controls as a safe fallback.

## Status

TouchSignal is in architecture and hardware-validation work. It does not yet
ship the full HUD, but it has an installable supervised Touch Bar owner runtime
and reversible user-service helpers. The runtime still must not be enabled or
attached to live hardware without an explicit operator action.

The initial target is intentionally narrow:

- Arch Linux with the `linux-t2` kernel
- Omarchy and Hyprland
- Intel MacBookPro16,1
- the installed Herdr API
- the Codex desktop app

## Version 0.1

Version 0.1 is complete when the Touch Bar continuously displays:

- one Codex app tile;
- the main agents from up to four Herdr workspaces in Herdr order;
- accurate status signs and semantic color from verified sources;
- tap-to-launch or tap-to-focus behavior for every visible tile; and
- an Fn/media layer plus automatic return to the firmware row when TouchSignal
  cannot own the hardware safely.

Version 0.1 does not include a command palette, workflow launcher, approval
control, reasoning selector, subagent browser, arbitrary prompt input, or swipe
navigation.

## Safety boundary

Only one process may own the Touch Bar DRM and touch-input devices. TouchSignal
must never run beside tiny-dfr, react-drm-for-touchbar, mac-touchbar-plus, or
another direct renderer.

The current machine already has a kernel and firmware Fn/media row. TouchSignal
must leave that row untouched when its prerequisites are unavailable and restore
it after logout, suspend failure, renderer failure, or an intentional stop.

## Touch Bar owner spike

Issue #2 selected the react-drm device model as the exclusive owner foundation
and kept tiny-dfr as the fallback direction. The native test surface is the
measured MacBookPro16,1 mode of 2008 by 60 pixels. Live attach, physical touch,
idle CPU, and suspend/resume remain open on issue #2. The spike tooling lives in
`src/touchbar_owner/`. Restore USB configuration 1 with:

```bash
PYTHONPATH=src python3 -m touchbar_owner.cli restore
```

See [the owner spike notes](docs/hardware/touchbar-owner-spike.md) and
[ADR 0001](docs/adr/0001-touchbar-owner-foundation.md).

## Supervised runtime

The supervised runtime checks the supported MacBookPro16,1 graphical session,
required kernel modules, and device permissions before it claims hardware. It
restores the firmware row before stop, logout, failed attach, and supervised
restart.

While the owner is healthy, it reads the internal keyboard's `KEY_FN` events
without grabbing the keyboard. Fn selects a seven-button media layer backed by
an owner-held virtual keyboard. Locked or unavailable Omarchy lock state selects
the same privacy-safe media layer; unlocking refreshes workflow state before
agent tiles return.

Install or remove the user service with:

```bash
sudo install -Dm0644 systemd/modules-load.d/touchsignal.conf \
  /etc/modules-load.d/touchsignal.conf
sudo modprobe uinput
python3 scripts/install-touchsignal-runtime
python3 scripts/remove-touchsignal-runtime
```

The privileged prerequisite makes `/dev/uinput` available before the enabled
user service starts after a reboot. The two Python commands write or remove only
the user unit under `~/.config/systemd/user/`. Removal disables the service and
runs the firmware-row restore command. To reverse the boot prerequisite, remove
`/etc/modules-load.d/touchsignal.conf`; this does not unload the module from the
current boot.

## Documents

- [Architecture analysis](docs/architecture.md)
- [Upstream implementation research](docs/research/upstream.md)
- [Touch Bar owner spike](docs/hardware/touchbar-owner-spike.md)
- [ADR 0001: Touch Bar owner foundation](docs/adr/0001-touchbar-owner-foundation.md)
