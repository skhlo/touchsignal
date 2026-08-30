# TouchSignal architecture analysis

This document records the verified design boundary for TouchSignal 0.1.
It separates observed machine facts from proposed implementation decisions.

## Product boundary

TouchSignal is an integration for Omarchy on the Intel MacBookPro16,1. It is not
a general agent control plane, a replacement for Herdr, or an Omarchy shell
fork. Herdr, Hyprland, and the ChatGPT desktop app are adapters around one small
physical interface. Omarchy supplies the environment and stock system marks,
not a live color palette.

The intended feeling is calm and immediate. A tile responds when touched,
commits only on release, states exactly what is known, and never traps the user
away from the original keyboard controls.

## Verified local baseline

The following facts were observed on 2026-08-29 without changing system state.

| Surface | Observed state | Design consequence |
| --- | --- | --- |
| Hardware | DMI reports `MacBookPro16,1`. | Version 0.1 may gate explicitly on this model. |
| Kernel | `7.1.8-arch1-Watanare-T2-3-t2` from `linux-t2`. | The first hardware test must target this exact kernel line. |
| Firmware row | `hid_appletb_kbd` is loaded in special-key mode `2`, Fn toggling is enabled, and automatic dimming is enabled. | A known-good Fn/media fallback already exists without tiny-dfr. |
| Backlight | `hid_appletb_bl` is loaded at brightness `2`. | Fallback restoration must restore brightness as well as key mode. |
| DRM | `appletbdrm` is installed but unloaded. The visible DRM cards belong to Intel and AMD graphics. | Loading the module and claiming the new Touch Bar DRM card is an explicit, reversible hardware test. |
| Input | The firmware layer exposes `Apple Inc. Touch Bar Display` as `/dev/input/event7` and a keyboard device, not a general touch surface. | A custom owner must rediscover the touch device after `appletbdrm` attaches instead of caching `event7`. |
| Existing renderer | No tiny-dfr package, service, or other Touch Bar renderer is installed or running. | The project must preserve the kernel row, not assume a tiny-dfr service is the local fallback. |
| Omarchy | Omarchy `4.0.1` is active and the stock agents mark is available through the bar's font alias. | Reuse stock marks read-only while keeping TouchSignal's contrast independent from theme changes. |
| Herdr | Herdr `0.8.2`, protocol `20`, exposes session snapshots, workspace order, focus, lifecycle status, stable IDs, commands, and event subscriptions. | Herdr is the authoritative source and action surface for workspace tiles. |
| ChatGPT app | Hyprland reports the running app with class `chatgpt`. | Version 0.1 can verify closed, open, and focused states, but not internal task lifecycle. |
| Thermals | CPU package temperature is exposed by `coretemp`; the AMD dGPU exposes runtime power state separately from its `edge` temperature sensor. | Runtime state is the mandatory oracle before any dGPU temperature access. |

## Architecture

One user service owns the product behavior. Its modules remain separate even if
they initially run in one process.

```text
Hyprland IPC -------> ChatGPT adapter ----+
Herdr API ----------> Herdr adapter ------+--> normalized state --> tile model
logind session -----> lifecycle adapter --+                         |
                                                                    v
firmware row <--- detach and fallback <-- supervisor <-- renderer and input owner
                                                                    |
                                                                    v
                                                    appletbdrm + touch input
```

### Physical owner

The renderer is the only module allowed to open the Touch Bar DRM device, raw
touch input, backlight, or uinput. Adapters never receive hardware handles.

The current leading implementation foundation is
`react-drm-for-touchbar`, because it targets T2 MacBooks and already implements
DRM/Cairo rendering, touch input, Hyprland awareness, browser preview, and
suspend/resume handling. It is not yet an accepted dependency. It is young,
GPL-3.0-or-later, and would replace every competing renderer. A reversible
compatibility spike must prove attach, detach, idle cost, and resume behavior on
this machine before that decision is made.

If that spike fails, the fallback design direction is a smaller Rust owner based
on tiny-dfr's proven device model. Running both is never an option.

The exclusive-owner spike selected the react-drm device model and recorded that
choice in [ADR 0001](adr/0001-touchbar-owner-foundation.md). The production
runtime still must not vendor the react-drm control center or run tiny-dfr
beside TouchSignal.

### Normalized state

Adapters publish values into one capability-gated state model. They do not draw
tiles and they do not infer states their source cannot prove.

Herdr lifecycle values remain exactly:

- `idle` -> `Ready` with a dot sign
- `working` -> `Working` with an arrow sign
- `blocked` -> `Needs input` with an exclamation sign
- `done` -> `Done` with a check sign
- `unknown` -> `Unknown` with a question sign

Each agent tile presents a logo and a non-color status sign in equally sized
visual boxes. Color reinforces the sign but never replaces it. Accessible
previews and diagnostic output use the full status label.

Identity is strictly monochrome. The OLED panel and normal buttons are black;
workspace numbers, logos, fallback marks, Ready signs, and Unknown signs are
white. Needs input and Unavailable invert the whole button to white with black
identity marks because those states require attention. The fixed lifecycle
palette uses blue for Working, amber for Needs input, green for Done, and red
for Unavailable. Pressed feedback uses neutral gray.

The ChatGPT desktop adapter is deliberately narrower. With the verified local
surface, it may report only `Closed`, `Open`, and `Focused`. It must not claim
that the app is working, idle, or blocked. Agent lifecycle belongs to Herdr
workspace tiles, including later Codex, Claude, Pi, and Grok agent identities.

### Touch Bar layout

The native MacBookPro16,1 surface is logical 2008 by 60 pixels, matching the
measured appletbdrm mode of 60 by 2008. That size is model-specific. It is not
a generic Touch Bar size and it is not a fallback from 2170 by 60.

The workflow layer has three stable regions.

1. The left agent dock contains one persistent ChatGPT app tile followed by four
   stable Herdr workspace slots. Empty Herdr slots preserve geometry. A full
   four-workspace state therefore has five visible agent tiles.
2. The center is reserved for future contextual information. Version 0.1 does not
   move the agent dock into this space.
3. The right hardware cluster contains only CPU temperature, GPU temperature,
   and the power-profile control.

Each agent hit target is 112 by 46 pixels. The logo and status sign sit side by
side in equal 30-pixel boxes. Prototype letters stand in for final agent logos.
Herdr tiles retain the workspace number so repeated agent identities remain
distinguishable.

Button one keeps the ChatGPT focus and launch action but uses the stock Omarchy
agents robot glyph from the status bar as its visual mark.

CPU and GPU temperatures are read-only. The GPU adapter reads dGPU runtime state
first and accesses the temperature source only when that state already reports
active. When the dGPU is suspended or its state is unknown, the GPU temperature
tile dims and shows `--°C`; there is no separate dGPU power tile. A hardware
test must verify that the runtime-state query itself does not wake a suspended
device before this polling path is accepted.

Thermal sources sample every two seconds. Presentation republishes immediately
when availability, dGPU runtime state, severity band, or a three-degree change
occurs, and at least once every 30 seconds otherwise. This keeps exact values
fresh without repainting the full DRM surface for one-degree sensor jitter.

### Thermal safety trust envelope

| Invariant | Strength | Home | Oracle and seam | Disposition and proof |
| --- | --- | --- | --- | --- |
| A dGPU temperature read occurs only after the immediately preceding runtime verdict is `active`. | Enforced | `LiveThermalAdapter.snapshot()` | Injected file reader records every path access. | Placed. Active tests prove runtime-before-temperature order; suspended, unknown, unreadable, and discovery-bypass tests prove the temperature path is never reached. |
| CPU and GPU tiles never emit a hardware action. | Enforced | Workflow touch routing | Product-seam actions are observed after touches in both thermal targets. | Placed. Thermal target tests produce no agent, workspace, or hardware action. |
| CPU, GPU, and future power positions remain stable across source states. | Enforced | Workflow frame geometry | Product-seam frame targets. | Placed. Geometry tests cover the three right-cluster slots from unavailable through active snapshots. |

The active on-device path resolves the CPU package sensor, observes an active
dGPU runtime verdict, and displays the verified AMD edge temperature. The
suspended on-device proof remains provisional because the current compositor
session holds the AMD DRM device and prevents runtime suspension even when
runtime power control is temporarily set to automatic. The test must be rerun
in an iGPU-only compositor session; the temporary power-control probe restored
its original value.

The power-profile tile is the only interactive hardware tile. It opens explicit
`Power saver`, `Balanced`, `Performance`, and `Cancel` choices and changes its
label only after the system source verifies the selected profile.

### Herdr workspace selection

The Herdr adapter starts from the installed API snapshot and follows the
installed schema instead of caching command syntax.

1. Sort workspaces by Herdr's numeric `number` field.
2. Keep the first four.
3. Use each workspace's `active_tab_id` and that tab layout's
   `focused_pane_id` to identify its main pane.
4. If that pane hosts an agent, use its agent identity for the tile label.
5. Display the authoritative workspace aggregate `agent_status`, so a blocked
   background agent is not hidden by a merely idle focused pane.
6. If no agent is available, show the workspace label and its reported state
   without inventing an agent identity.

The live API schema supports workspace, tab, pane, layout, focus, and agent
status events through `events.subscribe`. TouchSignal should take one complete
snapshot, subscribe, and resnapshot after topology changes. Bounded polling of
`herdr api snapshot` is a compatibility fallback, not the primary design.

Tap focuses the selected agent pane through Herdr's supported agent-focus
surface. If the pane has no agent, it focuses the workspace. IDs always come
from the current snapshot.

### ChatGPT app behavior

The ChatGPT app tile is always present.

- If Hyprland reports a `chatgpt` client, tap focuses that client.
- If it is absent, tap uses the installed desktop launcher and remains in an
  `Opening` presentation state until Hyprland confirms the window.
- A timeout becomes `Unavailable`; it never becomes a false `Open` state merely
  because a process was spawned.

The exact launch command will be discovered from the installed desktop entry at
implementation time rather than embedded as an Omarchy-specific shell command.
Hyprland state and focus actions use its synchronous Unix IPC socket directly;
the adapter closes every request connection immediately and falls back to
`hyprctl` only when the socket is unavailable.

## Omarchy integration

TouchSignal remains independent from the Omarchy shell.

### Fixed contrast

The Touch Bar is its own OLED display, so desktop theme colors do not provide
useful context and can reduce small-logo contrast. TouchSignal therefore keeps
normal buttons black, reserves full white inversion for attention states, and
uses one fixed semantic status palette across dark and light Omarchy themes. It
does not watch theme files or install a theme hook.

TouchSignal may read stock Omarchy glyph and font definitions to match familiar
system marks. It never edits `/usr/share/omarchy/` or requires a cloned shell
plugin for Touch Bar rendering.

TouchSignal's own configuration belongs under `~/.config/touchsignal/`, not
`~/.config/omarchy/`.

## Interaction contract

Version 0.1 supports taps and the Fn layer only. It has no swipe, long-press, or
double-tap gesture.

For each tile:

1. Touch-down shows pressed feedback immediately without taking action.
2. Movement remains eligible within a forgiving boundary around the tile.
3. Moving away cancels the press; moving back before release restores it.
4. Release inside commits exactly one launch or focus action.
5. Cancellation clears the pressed state without action.
6. The tile remains in a pending presentation until its source verifies the
   outcome.

State changes use an immediate update or a short cross-fade. They do not slide
the entire row or use decorative looping motion. The tile layout remains stable
as labels and states update.

The owner waits on the touch file descriptor with a 500-millisecond idle source
refresh timeout. A real touch wakes the wait immediately, while idle operation
avoids a fixed high-frequency polling loop.

TouchSignal is supplemental. Every action remains available through the normal
keyboard, Herdr, Hyprland, ChatGPT, and coding-agent interfaces. A Touch Bar
failure cannot be the only route to an action.

## Fn and failure fallback

Fallback has two layers:

1. While TouchSignal is healthy, Fn exposes a static function/media layer with
   brightness, playback, and volume controls. It does not duplicate the
   MacBookPro16,1 physical Escape key.
2. When TouchSignal stops, fails, logs out, or cannot reattach after resume, its
   detach helper releases the devices and restores the kernel firmware row,
   special-key mode, Fn toggling, automatic dimming, and brightness.

The supervisor starts only after graphical login and uses restart-on-failure.
It quiesces and closes hardware before suspend, rediscovers devices after
resume, and restores the firmware row if reattachment fails. It must not start
on an unsupported model or without the required kernel modules and permissions.

When the session locks, agent labels and actions are hidden. TouchSignal shows a
neutral media layer or blanks the panel and rejects workflow taps until the
session unlocks.

## Version 0.1 acceptance gates

Version 0.1 is not complete until all of these pass on the actual MacBookPro16,1:

- cold login starts exactly one renderer;
- unsupported or missing hardware leaves the firmware row working;
- killing the renderer restores the firmware row and the supervised restart
  reacquires the devices without two owners;
- logout restores the firmware row;
- three suspend/resume cycles preserve touch, display, and fallback behavior;
- switching between dark and light Omarchy themes leaves the fixed high-contrast
  row legible without restarting the renderer;
- idle CPU use is event-driven and remains below one percent of one core over a
  five-minute measurement;
- the ChatGPT app tile launches and focuses only after Hyprland verification;
- one through four, and more than four, Herdr workspaces render in Herdr order;
- the ChatGPT app tile remains visible beside all four Herdr workspace tiles;
- the center remains reserved while CPU temperature, GPU temperature, and power
  profile remain the only right-side tiles;
- a suspended dGPU dims the GPU tile without a temperature read or wake event;
- every Herdr lifecycle state displays its accurate sign and semantic color,
  with the full label available in diagnostic output;
- duplicate workspace names remain distinguishable by workspace number;
- touch-down, release, cancellation, and drag-away behavior match the interaction
  contract;
- Fn exposes familiar media controls while TouchSignal is healthy;
- locking the session hides agent information and disables workflow actions;
- loss of Herdr or Hyprland degrades to explicit `Unavailable` or `Unknown`
  states without affecting the Fn/media layer; and
- pixel shifting or another proven burn-in mitigation remains active without
  visible jitter.

## Deferred work

The following remain outside version 0.1:

- command palettes and workflow launchers;
- approval or prompt controls;
- reasoning and model selectors;
- subagent navigation;
- arbitrary text entry;
- swipe navigation and momentum interaction;
- general support for other MacBook models, desktop environments, or operating
  systems; and
- an unrestricted third-party adapter protocol.

## Evidence

See [upstream implementation research](research/upstream.md) for cited source
analysis of tiny-dfr, react-drm-for-touchbar, mac-touchbar-plus, OpenMicro, and
Microbridge.

See [the owner spike notes](hardware/touchbar-owner-spike.md) for the recovery
command and reversal procedure.
