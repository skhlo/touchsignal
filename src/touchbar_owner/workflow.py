from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from typing import Protocol

from .chatgpt import ChatGPTActions, HyprlandClient, HyprlandSnapshot, HyprlandSource
from .types import NATIVE_HEIGHT, NATIVE_WIDTH, RuntimeFrame, TouchEvent


CHATGPT_APP_LOGO_ASSET = "assets/apps/chatgpt-logo-white.svg"
CHATGPT_TARGET_WIDTH = 112
CHATGPT_TARGET_HEIGHT = 46
VISUAL_BOX_SIZE = 27
TOUCH_SLOP = 10
DEFAULT_PENDING_TIMEOUT = 5.0


class ChatGPTState(StrEnum):
    CLOSED = "Closed"
    OPEN = "Open"
    FOCUSED = "Focused"
    OPENING = "Opening"
    UNAVAILABLE = "Unavailable"


class PendingKind(StrEnum):
    LAUNCH = "launch"
    FOCUS = "focus"


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
    logo_asset: str
    state: ChatGPTState
    status_sign: str
    pressed: bool = False
    pending: bool = False
    touch_cancelled: bool = False
    capability_available: bool = True


@dataclass(frozen=True)
class WorkflowFrame:
    surface_size: tuple[int, int]
    chatgpt_tile: ChatGPTTileFrame
    reserved_center: Geometry
    safe_system_layer_available: bool = True


@dataclass(frozen=True)
class PendingRequest:
    kind: PendingKind
    started_at: float
    client_address: str | None = None


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
    ) -> None:
        self.source = source
        self.actions = actions
        self.clock = clock
        self.pending_timeout = pending_timeout
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
                logo_asset=CHATGPT_APP_LOGO_ASSET,
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


class WorkflowRenderer:
    def __init__(self, workflow: ChatGPTWorkflow) -> None:
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


def chatgpt_visual_boxes(target: Geometry) -> tuple[Geometry, Geometry]:
    gap = 9
    pair_width = VISUAL_BOX_SIZE * 2 + gap
    start_x = target.x + (target.width - pair_width) // 2
    y = target.y + (target.height - VISUAL_BOX_SIZE) // 2
    logo = Geometry(start_x, y, VISUAL_BOX_SIZE, VISUAL_BOX_SIZE)
    status = Geometry(start_x + VISUAL_BOX_SIZE + gap, y, VISUAL_BOX_SIZE, VISUAL_BOX_SIZE)
    return logo, status


def _first_chatgpt_client(snapshot: HyprlandSnapshot) -> HyprlandClient | None:
    clients = snapshot.chatgpt_clients
    return clients[0] if clients else None


def _status_sign(state: ChatGPTState) -> str:
    signs = {
        ChatGPTState.CLOSED: "dot",
        ChatGPTState.OPEN: "arrow",
        ChatGPTState.FOCUSED: "check",
        ChatGPTState.OPENING: "pending",
        ChatGPTState.UNAVAILABLE: "bang",
    }
    return signs[state]
