# TouchSignal

TouchSignal is an Omarchy-aware Touch Bar HUD for agent workflows on an Intel
MacBook Pro 16,1.

It turns the otherwise static Touch Bar into a small, direct status and focus
surface for the Codex app and Herdr workspaces while preserving the machine's
Fn and media controls as a safe fallback.

## Status

TouchSignal is in architecture and hardware-validation work. It does not yet
ship an installable renderer, service, or system configuration.

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

## Documents

- [Architecture analysis](docs/architecture.md)
- [Upstream implementation research](docs/research/upstream.md)
- [Touch Bar owner spike](docs/hardware/touchbar-owner-spike.md)
- [ADR 0001: Touch Bar owner foundation](docs/adr/0001-touchbar-owner-foundation.md)
