from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from typing import Protocol

from .chatgpt import ChatGPTActions, HyprlandClient, HyprlandSnapshot, HyprlandSource
from .herdr import (
    HerdrActions,
    HerdrPane,
    HerdrSnapshot,
    HerdrSource,
    HerdrWorkspace,
)
from .thermal import GpuRuntimeState, ThermalSnapshot, ThermalSource
from .types import NATIVE_HEIGHT, NATIVE_WIDTH, RuntimeFrame, TouchEvent


BUTTON_ONE_GLYPH = "󱚣"
BUTTON_ONE_FONT_FAMILY = "monospace"
CHATGPT_TARGET_WIDTH = 112
CHATGPT_TARGET_HEIGHT = 46
HERDR_TARGET_WIDTH = 112
HERDR_TARGET_HEIGHT = 46
HERDR_SLOT_COUNT = 4
VISUAL_BOX_SIZE = 30
TOUCH_SLOP = 10
DEFAULT_PENDING_TIMEOUT = 5.0
DEFAULT_FAILURE_DISPLAY_TIMEOUT = 2.0
CPU_TARGET_WIDTH = 148
GPU_TARGET_WIDTH = 148
POWER_TARGET_WIDTH = 142
HARDWARE_TARGET_HEIGHT = 46
HARDWARE_GAP = 6
HARDWARE_RIGHT_PADDING = 8
CPU_WARM_C = 80
CPU_HOT_C = 92
GPU_WARM_C = 76
GPU_HOT_C = 90


class ChatGPTState(StrEnum):
    CLOSED = "Closed"
    OPEN = "Open"
    FOCUSED = "Focused"
    OPENING = "Opening"
    UNAVAILABLE = "Unavailable"


class HerdrState(StrEnum):
    READY = "Ready"
    WORKING = "Working"
    BLOCKED = "Needs input"
    DONE = "Done"
    UNKNOWN = "Unknown"
    UNAVAILABLE = "Unavailable"


AGENT_STATUS_TO_STATE = {
    "idle": HerdrState.READY,
    "working": HerdrState.WORKING,
    "blocked": HerdrState.BLOCKED,
    "done": HerdrState.DONE,
    "unknown": HerdrState.UNKNOWN,
}

STATE_SIGNS = {
    HerdrState.READY: "dot",
    HerdrState.WORKING: "arrow",
    HerdrState.BLOCKED: "bang",
    HerdrState.DONE: "check",
    HerdrState.UNKNOWN: "question",
    HerdrState.UNAVAILABLE: "bang",
}

STATE_TOKENS = {
    HerdrState.READY: "ready",
    HerdrState.WORKING: "working",
    HerdrState.BLOCKED: "blocked",
    HerdrState.DONE: "done",
    HerdrState.UNKNOWN: "unknown",
    HerdrState.UNAVAILABLE: "unavailable",
}

AGENT_LOGO_ASSETS = {
    "codex": "assets/agents/codex-logo-white.svg",
    "claude": "assets/agents/claude-logo-light.png",
    "pi": "assets/agents/pi-coding-agent.svg",
    "grok": "assets/agents/grok-logo-dark.svg",
}

IDENTITY_FALLBACKS = {
    "codex": "CX",
    "claude": "CL",
    "pi": "PI",
    "grok": "GK",
}


class PendingKind(StrEnum):
    LAUNCH = "launch"
    FOCUS = "focus"
    AGENT_FOCUS = "agent_focus"
    WORKSPACE_FOCUS = "workspace_focus"


@dataclass(frozen=True)
class Geometry:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width - 1

    @property
    def bottom(self) -> int:
        return self.y + self.height - 1

    def contains(self, x: int, y: int, slop: int = 0) -> bool:
        return (
            self.x - slop <= x <= self.right + slop
            and self.y - slop <= y <= self.bottom + slop
        )


@dataclass(frozen=True)
class ChatGPTTileFrame:
    target: Geometry
    logo_box: Geometry
    status_box: Geometry
    logo_asset: str | None
    logo_glyph: str | None
    logo_font_family: str | None
    state: ChatGPTState
    status_sign: str
    pressed: bool = False
    pending: bool = False
    touch_cancelled: bool = False
    capability_available: bool = True


@dataclass(frozen=True)
class HerdrTileFrame:
    target: Geometry
    logo_box: Geometry | None
    status_box: Geometry | None
    logo_asset: str | None
    agent_identity: str | None
    identity_fallback: str | None
    workspace_number: int | None
    state: HerdrState
    status_sign: str
    state_token: str
    workspace_label: str | None = None
    pressed: bool = False
    touch_cancelled: bool = False
    pending: bool = False
    focus_timeout: bool = False
    focus_failed: bool = False
    source_lost: bool = False


@dataclass(frozen=True)
class EmptyTileFrame:
    target: Geometry


@dataclass(frozen=True)
class TemperatureTileFrame:
    target: Geometry
    label: str
    value: str
    celsius: int | None
    available: bool
    dimmed: bool
    attention: bool
    band: str
    runtime_state: str | None = None


@dataclass(frozen=True)
class PendingRequest:
    kind: PendingKind
    started_at: float
    slot: int | None = None
    client_address: str | None = None
    workspace_id: str | None = None
    pane_id: str | None = None


@dataclass(frozen=True)
class WorkflowFrame:
    surface_size: tuple[int, int]
    chatgpt_tile: ChatGPTTileFrame
    herdr_tiles: tuple[HerdrTileFrame, ...] = ()
    empty_slots: tuple[EmptyTileFrame, ...] = ()
    cpu_temperature: TemperatureTileFrame | None = None
    gpu_temperature: TemperatureTileFrame | None = None
    power_slot: EmptyTileFrame | None = None
    reserved_center: Geometry | None = None
    safe_system_layer_available: bool = True


@dataclass(frozen=True)
class PressState:
    target: str
    eligible: bool


class Clock(Protocol):
    def __call__(self) -> float: ...


class ChatGPTWorkflow:
    def __init__(
        self,
        source: HyprlandSource,
        actions: ChatGPTActions,
        *,
        clock: Clock = monotonic,
        pending_timeout: float = DEFAULT_PENDING_TIMEOUT,
        failure_display_timeout: float = DEFAULT_FAILURE_DISPLAY_TIMEOUT,
    ) -> None:
        self.source = source
        self.actions = actions
        self.clock = clock
        self.pending_timeout = pending_timeout
        self.failure_display_timeout = failure_display_timeout
        self.pending: PendingRequest | None = None
        self.press: PressState | None = None
        self.last_snapshot = HyprlandSnapshot.unavailable()
        self.timed_out_request: PendingRequest | None = None

    def process_touch_events(self, events: list[TouchEvent]) -> None:
        self.refresh()
        for event in events:
            if event.kind == "down":
                self._touch_down(event)
            elif event.kind == "move":
                self._touch_move(event)
            elif event.kind == "up":
                self._touch_up(event)
        self.refresh()

    def refresh(self) -> None:
        self.last_snapshot = self.source.snapshot()
        self._settle_pending()
        self._settle_timed_out_request()

    def frame(self) -> WorkflowFrame:
        self.refresh()
        state = self._presentation_state()
        target = chatgpt_target_geometry()
        logo_box, status_box = chatgpt_visual_boxes(target)
        pressed = self.press is not None and self.press.eligible
        cancelled = self.press is not None and not self.press.eligible
        return WorkflowFrame(
            surface_size=(NATIVE_WIDTH, NATIVE_HEIGHT),
            chatgpt_tile=ChatGPTTileFrame(
                target=target,
                logo_box=logo_box,
                status_box=status_box,
                logo_asset=None,
                logo_glyph=BUTTON_ONE_GLYPH,
                logo_font_family=BUTTON_ONE_FONT_FAMILY,
                state=state,
                status_sign=_status_sign(state),
                pressed=pressed,
                pending=self.pending is not None,
                touch_cancelled=cancelled,
                capability_available=self.last_snapshot.available,
            ),
            reserved_center=Geometry(
                x=target.x + target.width,
                y=0,
                width=NATIVE_WIDTH - (target.x + target.width),
                height=NATIVE_HEIGHT,
            ),
            safe_system_layer_available=True,
        )

    def _touch_down(self, event: TouchEvent) -> None:
        target = chatgpt_target_geometry()
        if target.contains(event.x, event.y):
            self.press = PressState(target="chatgpt", eligible=True)

    def _touch_move(self, event: TouchEvent) -> None:
        if self.press is None:
            return
        eligible = chatgpt_target_geometry().contains(event.x, event.y, slop=TOUCH_SLOP)
        self.press = PressState(target=self.press.target, eligible=eligible)

    def _touch_up(self, event: TouchEvent) -> None:
        if self.press is None:
            return
        eligible = chatgpt_target_geometry().contains(event.x, event.y, slop=TOUCH_SLOP)
        self.press = None
        if eligible:
            self._commit_chatgpt_action()

    def _commit_chatgpt_action(self) -> None:
        if self.pending is not None or not self.last_snapshot.available:
            return
        if self.timed_out_request is not None:
            return
        focused = self.last_snapshot.focused_chatgpt
        if focused is not None:
            return
        client = _first_chatgpt_client(self.last_snapshot)
        now = self.clock()
        if client is None:
            self.actions.request_launch()
            self.pending = PendingRequest(PendingKind.LAUNCH, started_at=now)
            return
        self.actions.request_focus(client)
        self.pending = PendingRequest(
            PendingKind.FOCUS,
            started_at=now,
            client_address=client.address,
        )

    def _settle_pending(self) -> None:
        if self.pending is None:
            return
        if self.clock() - self.pending.started_at >= self.pending_timeout:
            self.timed_out_request = self.pending
            self.pending = None
            return
        if self.pending.kind == PendingKind.LAUNCH and self.last_snapshot.chatgpt_clients:
            self.pending = None
            return
        if self.pending.kind == PendingKind.FOCUS:
            focused = self.last_snapshot.focused_chatgpt
            if focused is not None and focused.address == self.pending.client_address:
                self.pending = None

    def _settle_timed_out_request(self) -> None:
        if self.timed_out_request is None:
            return
        if self.timed_out_request.kind == PendingKind.LAUNCH and self.last_snapshot.chatgpt_clients:
            self.timed_out_request = None
            return
        if self.timed_out_request.kind == PendingKind.FOCUS:
            focused = self.last_snapshot.focused_chatgpt
            if focused is not None and focused.address == self.timed_out_request.client_address:
                self.timed_out_request = None
                return
        if (
            self.last_snapshot.available
            and self.clock() - self.timed_out_request.started_at
            >= self.pending_timeout + self.failure_display_timeout
        ):
            self.timed_out_request = None

    def _presentation_state(self) -> ChatGPTState:
        if not self.last_snapshot.available or self.timed_out_request is not None:
            return ChatGPTState.UNAVAILABLE
        if self.pending is not None and self.pending.kind == PendingKind.LAUNCH:
            return ChatGPTState.OPENING
        if self.last_snapshot.focused_chatgpt is not None:
            return ChatGPTState.FOCUSED
        if self.last_snapshot.chatgpt_clients:
            return ChatGPTState.OPEN
        return ChatGPTState.CLOSED


class RenderableWorkflow(Protocol):
    def process_touch_events(self, events: list[TouchEvent]) -> None: ...

    def frame(self) -> WorkflowFrame: ...


class WorkflowRenderer:
    def __init__(self, workflow: RenderableWorkflow) -> None:
        self.workflow = workflow

    def render(self, touches: list[TouchEvent]) -> RuntimeFrame:
        self.workflow.process_touch_events(touches)
        return RuntimeFrame(
            surface_size=(NATIVE_WIDTH, NATIVE_HEIGHT),
            touch_count=len(touches),
            last_touch=touches[-1] if touches else None,
            workflow_frame=self.workflow.frame(),
        )


def chatgpt_target_geometry() -> Geometry:
    return Geometry(
        x=0,
        y=(NATIVE_HEIGHT - CHATGPT_TARGET_HEIGHT) // 2,
        width=CHATGPT_TARGET_WIDTH,
        height=CHATGPT_TARGET_HEIGHT,
    )


def herdr_slot_geometry(index: int) -> Geometry:
    return Geometry(
        x=CHATGPT_TARGET_WIDTH + index * HERDR_TARGET_WIDTH,
        y=(NATIVE_HEIGHT - HERDR_TARGET_HEIGHT) // 2,
        width=HERDR_TARGET_WIDTH,
        height=HERDR_TARGET_HEIGHT,
    )


def power_slot_geometry() -> Geometry:
    return Geometry(
        x=NATIVE_WIDTH - HARDWARE_RIGHT_PADDING - POWER_TARGET_WIDTH,
        y=(NATIVE_HEIGHT - HARDWARE_TARGET_HEIGHT) // 2,
        width=POWER_TARGET_WIDTH,
        height=HARDWARE_TARGET_HEIGHT,
    )


def gpu_temperature_geometry() -> Geometry:
    power = power_slot_geometry()
    return Geometry(
        x=power.x - HARDWARE_GAP - GPU_TARGET_WIDTH,
        y=(NATIVE_HEIGHT - HARDWARE_TARGET_HEIGHT) // 2,
        width=GPU_TARGET_WIDTH,
        height=HARDWARE_TARGET_HEIGHT,
    )


def cpu_temperature_geometry() -> Geometry:
    gpu = gpu_temperature_geometry()
    return Geometry(
        x=gpu.x - HARDWARE_GAP - CPU_TARGET_WIDTH,
        y=(NATIVE_HEIGHT - HARDWARE_TARGET_HEIGHT) // 2,
        width=CPU_TARGET_WIDTH,
        height=HARDWARE_TARGET_HEIGHT,
    )


def chatgpt_visual_boxes(target: Geometry) -> tuple[Geometry, Geometry]:
    gap = 7
    pair_width = VISUAL_BOX_SIZE * 2 + gap
    start_x = target.x + (target.width - pair_width) // 2
    y = target.y + (target.height - VISUAL_BOX_SIZE) // 2
    logo = Geometry(start_x, y, VISUAL_BOX_SIZE, VISUAL_BOX_SIZE)
    status = Geometry(
        start_x + VISUAL_BOX_SIZE + gap, y, VISUAL_BOX_SIZE, VISUAL_BOX_SIZE
    )
    return logo, status


def _first_chatgpt_client(snapshot: HyprlandSnapshot) -> HyprlandClient | None:
    clients = snapshot.chatgpt_clients
    return clients[0] if clients else None


def _selected_workspaces(snapshot: HerdrSnapshot) -> tuple[HerdrWorkspace, ...]:
    ordered = sorted(snapshot.workspaces, key=lambda workspace: workspace.number)
    return tuple(ordered[:HERDR_SLOT_COUNT])


def _agent_logo_asset(identity: str | None) -> str | None:
    if identity is None:
        return None
    return AGENT_LOGO_ASSETS.get(identity.casefold())


def _identity_fallback(identity: str | None, state: HerdrState) -> str | None:
    if identity is None:
        return None
    if state == HerdrState.UNAVAILABLE:
        return None
    return IDENTITY_FALLBACKS.get(identity.casefold(), identity[:2].upper())


def _herdr_status_sign(state: HerdrState) -> str:
    return STATE_SIGNS[state]


def _herdr_state_token(state: HerdrState) -> str:
    return STATE_TOKENS[state]


def _temperature_value(celsius: int | None) -> str:
    return f"{celsius}°C" if celsius is not None else "--°C"


def _temperature_band(celsius: int | None, warm: int, hot: int) -> str:
    if celsius is None:
        return "unavailable"
    if celsius >= hot:
        return "hot"
    if celsius >= warm:
        return "warm"
    return "normal"


def _status_sign(state: ChatGPTState) -> str:
    signs = {
        ChatGPTState.CLOSED: "dot",
        ChatGPTState.OPEN: "arrow",
        ChatGPTState.FOCUSED: "check",
        ChatGPTState.OPENING: "pending",
        ChatGPTState.UNAVAILABLE: "bang",
    }
    return signs[state]


class HerdrWorkflow:
    def __init__(
        self,
        source: HerdrSource,
        actions: HerdrActions,
        *,
        chatgpt: ChatGPTWorkflow | None = None,
        thermal_source: ThermalSource | None = None,
        clock: Clock = monotonic,
        pending_timeout: float = DEFAULT_PENDING_TIMEOUT,
    ) -> None:
        self.source = source
        self.actions = actions
        self.clock = clock
        self.pending_timeout = pending_timeout
        self.pending: PendingRequest | None = None
        self.press: PressState | None = None
        self.last_snapshot = HerdrSnapshot.unavailable()
        self.timed_out_request: PendingRequest | None = None
        self.failed_request: PendingRequest | None = None
        self._last_selected: tuple[HerdrWorkspace, ...] = ()
        self._chatgpt = chatgpt
        self._thermal_source = thermal_source
        self.last_thermal_snapshot = ThermalSnapshot.unavailable()
        self._refresh_requested = True
        self.source.subscribe(self._request_refresh)

    def process_touch_events(self, events: list[TouchEvent]) -> None:
        self.refresh()
        for event in events:
            if self._chatgpt is not None and self._chatgpt.press is not None:
                self._chatgpt.process_touch_events([event])
                continue
            if self.press is not None:
                self._handle_herdr_touch(event)
                continue
            if event.kind != "down":
                continue
            if chatgpt_target_geometry().contains(event.x, event.y):
                if self._chatgpt is not None:
                    self._chatgpt.process_touch_events([event])
                continue
            self._handle_herdr_touch(event)
        self.refresh()

    def refresh(self) -> None:
        if self._refresh_requested:
            self._refresh_requested = False
            self.last_snapshot = self.source.snapshot()
            if self.last_snapshot.available:
                self._last_selected = _selected_workspaces(self.last_snapshot)
        if self._thermal_source is not None:
            self.last_thermal_snapshot = self._thermal_source.snapshot()
        self._settle_pending()
        self._settle_timed_out_request()
        self._settle_failed_request()

    def _request_refresh(self) -> None:
        self._refresh_requested = True

    def frame(self) -> WorkflowFrame:
        self.refresh()
        selected = self._last_selected
        occupied: list[HerdrTileFrame] = []
        empty: list[EmptyTileFrame] = []
        for index in range(HERDR_SLOT_COUNT):
            target = herdr_slot_geometry(index)
            if index < len(selected):
                occupied.append(self._tile_frame(index, selected[index], target))
            else:
                empty.append(EmptyTileFrame(target=target))
        last_target = herdr_slot_geometry(HERDR_SLOT_COUNT - 1)
        cpu_temperature, gpu_temperature = self._temperature_frames()
        cpu_target = cpu_temperature_geometry()
        return WorkflowFrame(
            surface_size=(NATIVE_WIDTH, NATIVE_HEIGHT),
            chatgpt_tile=self._chatgpt_frame(),
            herdr_tiles=tuple(occupied),
            empty_slots=tuple(empty),
            cpu_temperature=cpu_temperature,
            gpu_temperature=gpu_temperature,
            power_slot=EmptyTileFrame(target=power_slot_geometry()),
            reserved_center=Geometry(
                x=last_target.right + 1,
                y=0,
                width=cpu_target.x - (last_target.right + 1),
                height=NATIVE_HEIGHT,
            ),
            safe_system_layer_available=True,
        )

    def _temperature_frames(
        self,
    ) -> tuple[TemperatureTileFrame, TemperatureTileFrame]:
        snapshot = self.last_thermal_snapshot
        cpu = snapshot.cpu
        cpu_band = _temperature_band(cpu.celsius, CPU_WARM_C, CPU_HOT_C)
        cpu_frame = TemperatureTileFrame(
            target=cpu_temperature_geometry(),
            label="CPU",
            value=_temperature_value(cpu.celsius),
            celsius=cpu.celsius,
            available=cpu.available,
            dimmed=not cpu.available,
            attention=not cpu.available or cpu_band == "hot",
            band=cpu_band,
        )

        gpu = snapshot.gpu
        gpu_active = snapshot.gpu_runtime == GpuRuntimeState.ACTIVE
        gpu_available = gpu_active and gpu.available
        gpu_band = _temperature_band(gpu.celsius, GPU_WARM_C, GPU_HOT_C)
        gpu_frame = TemperatureTileFrame(
            target=gpu_temperature_geometry(),
            label="GPU",
            value=_temperature_value(gpu.celsius if gpu_available else None),
            celsius=gpu.celsius if gpu_available else None,
            available=gpu_available,
            dimmed=not gpu_available,
            attention=gpu_available and gpu_band == "hot",
            band=gpu_band if gpu_available else "normal",
            runtime_state=snapshot.gpu_runtime.value,
        )
        return cpu_frame, gpu_frame

    def _chatgpt_frame(self) -> ChatGPTTileFrame:
        if self._chatgpt is not None:
            return self._chatgpt.frame().chatgpt_tile
        target = chatgpt_target_geometry()
        logo_box, status_box = chatgpt_visual_boxes(target)
        return ChatGPTTileFrame(
            target=target,
            logo_box=logo_box,
            status_box=status_box,
            logo_asset=None,
            logo_glyph=BUTTON_ONE_GLYPH,
            logo_font_family=BUTTON_ONE_FONT_FAMILY,
            state=ChatGPTState.CLOSED,
            status_sign=_status_sign(ChatGPTState.CLOSED),
        )

    def _tile_frame(
        self,
        index: int,
        workspace: HerdrWorkspace,
        target: Geometry,
    ) -> HerdrTileFrame:
        pane = self._focused_pane(workspace)
        identity = pane.agent if pane is not None else None
        logo_box, status_box = chatgpt_visual_boxes(target)
        pressed = (
            self.press is not None
            and self.press.target == f"herdr-{index}"
            and self.press.eligible
        )
        cancelled = (
            self.press is not None
            and self.press.target == f"herdr-{index}"
            and not self.press.eligible
        )
        pending = self.pending is not None and self.pending.slot == index
        timed_out = (
            self.timed_out_request is not None and self.timed_out_request.slot == index
        )
        failed = self.failed_request is not None and self.failed_request.slot == index
        state = self._lifecycle_state(workspace)
        if timed_out or failed:
            state = HerdrState.UNAVAILABLE
        return HerdrTileFrame(
            target=target,
            logo_box=logo_box,
            status_box=status_box,
            logo_asset=_agent_logo_asset(identity),
            agent_identity=identity,
            identity_fallback=_identity_fallback(identity, state),
            workspace_number=workspace.number,
            workspace_label=workspace.label,
            state=state,
            status_sign=_herdr_status_sign(state),
            state_token=_herdr_state_token(state),
            pressed=pressed,
            touch_cancelled=cancelled,
            pending=pending,
            focus_timeout=timed_out,
            focus_failed=failed,
            source_lost=not self.last_snapshot.available,
        )

    def _focused_pane(self, workspace: HerdrWorkspace) -> HerdrPane | None:
        focused_id = self.last_snapshot.focused_pane_id(workspace.active_tab_id)
        if focused_id is None:
            return None
        return self.last_snapshot.pane(focused_id)

    def _lifecycle_state(self, workspace: HerdrWorkspace) -> HerdrState:
        if not self.last_snapshot.available:
            return HerdrState.UNAVAILABLE
        return AGENT_STATUS_TO_STATE.get(workspace.agent_status, HerdrState.UNKNOWN)

    def _touch_down(self, event: TouchEvent) -> None:
        selected = _selected_workspaces(self.last_snapshot)
        for index in range(len(selected)):
            target = herdr_slot_geometry(index)
            if target.contains(event.x, event.y):
                self.press = PressState(target=f"herdr-{index}", eligible=True)
                return

    def _handle_herdr_touch(self, event: TouchEvent) -> None:
        if event.kind == "down":
            self._touch_down(event)
        elif event.kind == "move":
            self._touch_move(event)
        elif event.kind == "up":
            self._touch_up(event)

    def _slot_for_target(self) -> int:
        assert self.press is not None
        return int(self.press.target.split("-")[1])

    def _touch_move(self, event: TouchEvent) -> None:
        if self.press is None:
            return
        if self.press.target == "chatgpt":
            eligible = chatgpt_target_geometry().contains(
                event.x, event.y, slop=TOUCH_SLOP
            )
        else:
            index = self._slot_for_target()
            eligible = herdr_slot_geometry(index).contains(
                event.x, event.y, slop=TOUCH_SLOP
            )
        self.press = PressState(target=self.press.target, eligible=eligible)

    def _touch_up(self, event: TouchEvent) -> None:
        if self.press is None:
            return
        if self.press.target == "chatgpt":
            eligible = chatgpt_target_geometry().contains(
                event.x, event.y, slop=TOUCH_SLOP
            )
            self.press = None
            if eligible:
                self._commit_chatgpt_action()
            return
        index = self._slot_for_target()
        eligible = herdr_slot_geometry(index).contains(
            event.x, event.y, slop=TOUCH_SLOP
        )
        self.press = None
        if eligible:
            self._commit_focus(index)

    def _commit_focus(self, index: int) -> None:
        if self.pending is not None or not self.last_snapshot.available:
            return
        if self.timed_out_request is not None:
            return
        self.failed_request = None
        selected = _selected_workspaces(self.last_snapshot)
        if index >= len(selected):
            return
        workspace = selected[index]
        pane = self._focused_pane(workspace)
        now = self.clock()
        if pane is not None and pane.agent is not None:
            request = PendingRequest(
                kind=PendingKind.AGENT_FOCUS,
                started_at=now,
                slot=index,
                workspace_id=workspace.workspace_id,
                pane_id=pane.pane_id,
            )
            if not self.actions.request_agent_focus(pane.pane_id):
                self.failed_request = request
                return
            self.pending = request
            return
        request = PendingRequest(
            kind=PendingKind.WORKSPACE_FOCUS,
            started_at=now,
            slot=index,
            workspace_id=workspace.workspace_id,
        )
        if not self.actions.request_workspace_focus(workspace.workspace_id):
            self.failed_request = request
            return
        self.pending = request

    def _settle_pending(self) -> None:
        if self.pending is None:
            return
        if self.clock() - self.pending.started_at >= self.pending_timeout:
            self.timed_out_request = self.pending
            self.pending = None
            return
        if self._focus_verified(self.pending):
            self.pending = None

    def _settle_timed_out_request(self) -> None:
        if self.timed_out_request is None:
            return
        if self._focus_verified(self.timed_out_request):
            self.pending = None
            self.timed_out_request = None

    def _settle_failed_request(self) -> None:
        if self.failed_request is None:
            return
        if self._focus_verified(self.failed_request):
            self.failed_request = None

    def _focus_verified(self, request: PendingRequest) -> bool:
        if request.kind == PendingKind.AGENT_FOCUS and request.pane_id is not None:
            pane = self.last_snapshot.pane(request.pane_id)
            return pane is not None and pane.focused is True
        if (
            request.kind == PendingKind.WORKSPACE_FOCUS
            and request.workspace_id is not None
        ):
            workspace = self._workspace_by_id(request.workspace_id)
            return workspace is not None and workspace.focused is True
        return False

    def _workspace_by_id(self, workspace_id: str) -> HerdrWorkspace | None:
        for workspace in self.last_snapshot.workspaces:
            if workspace.workspace_id == workspace_id:
                return workspace
        return None
