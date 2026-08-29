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

## Version 1

Version 1 is complete when the Touch Bar continuously displays:

- one Codex app tile;
- the main agents from up to three Herdr workspaces in Herdr order;
- accurate color and text status from verified sources;
- tap-to-launch or tap-to-focus behavior for every visible tile; and
- an Fn/media layer plus automatic return to the firmware row when TouchSignal
  cannot own the hardware safely.

Version 1 does not include a command palette, workflow launcher, approval
control, reasoning selector, subagent browser, arbitrary prompt input, or swipe
navigation.

## Safety boundary

Only one process may own the Touch Bar DRM and touch-input devices. TouchSignal
must never run beside tiny-dfr, react-drm-for-touchbar, mac-touchbar-plus, or
another direct renderer.

The current machine already has a kernel and firmware Fn/media row. TouchSignal
must leave that row untouched when its prerequisites are unavailable and restore
it after logout, suspend failure, renderer failure, or an intentional stop.

## Documents

- [Architecture analysis](docs/architecture.md)
- [Upstream implementation research](docs/research/upstream.md)
