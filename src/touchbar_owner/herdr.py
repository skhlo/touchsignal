from __future__ import annotations

import json
import os
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, Thread
from time import sleep
from typing import Protocol


TOPOLOGY_EVENT_KINDS = frozenset(
    {
        "workspace_created",
        "workspace_updated",
        "workspace_metadata_updated",
        "workspace_closed",
        "workspace_renamed",
        "workspace_moved",
        "workspace_reordered",
        "workspace_focused",
        "tab_created",
        "tab_closed",
        "tab_renamed",
        "tab_moved",
        "tab_focused",
        "pane_created",
        "pane_closed",
        "pane_updated",
        "pane_focused",
        "pane_moved",
        "pane_exited",
        "pane_agent_detected",
        "pane_agent_status_changed",
        "layout_updated",
    }
)

GLOBAL_REFRESH_EVENT_KINDS = TOPOLOGY_EVENT_KINDS - {"pane_agent_status_changed"}

HERDR_REFRESH_SUBSCRIPTIONS = tuple(
    {"type": kind.replace("_", ".", 1)}
    for kind in sorted(GLOBAL_REFRESH_EVENT_KINDS)
)

HerdrChangeCallback = Callable[[], None]


def resolve_herdr_socket_path(
    socket_path: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> str:
    environment = os.environ if environ is None else environ
    if socket_path:
        return socket_path
    configured = environment.get("HERDR_SOCKET_PATH")
    if configured:
        return configured
    config_home = environment.get("XDG_CONFIG_HOME")
    root = Path(config_home) if config_home else (home or Path.home()) / ".config"
    return str(root / "herdr" / "herdr.sock")


@dataclass(frozen=True)
class HerdrPane:
    pane_id: str
    workspace_id: str
    tab_id: str
    agent: str | None
    focused: bool = False


@dataclass(frozen=True)
class HerdrWorkspace:
    workspace_id: str
    number: int
    label: str
    active_tab_id: str
    agent_status: str
    focused: bool = False


@dataclass(frozen=True)
class HerdrSnapshot:
    available: bool
    workspaces: tuple[HerdrWorkspace, ...] = ()
    panes: tuple[HerdrPane, ...] = ()
    focused_pane_ids: dict[str, str] | None = None

    @classmethod
    def unavailable(cls) -> HerdrSnapshot:
        return cls(available=False)

    def focused_pane_id(self, tab_id: str) -> str | None:
        if self.focused_pane_ids is None:
            return None
        return self.focused_pane_ids.get(tab_id)

    def pane(self, pane_id: str) -> HerdrPane | None:
        for pane in self.panes:
            if pane.pane_id == pane_id:
                return pane
        return None


class HerdrSource(Protocol):
    def snapshot(self) -> HerdrSnapshot: ...

    def subscribe(self, on_change: HerdrChangeCallback) -> None: ...


class HerdrActions(Protocol):
    def request_agent_focus(self, pane_id: str) -> bool: ...

    def request_workspace_focus(self, workspace_id: str) -> bool: ...


def _parse_workspace(raw: object) -> HerdrWorkspace | None:
    if not isinstance(raw, dict):
        return None
    workspace_id = raw.get("workspace_id")
    number = raw.get("number")
    label = raw.get("label")
    active_tab_id = raw.get("active_tab_id")
    agent_status = raw.get("agent_status")
    focused = raw.get("focused", False)
    if not all(
        isinstance(value, str)
        for value in (workspace_id, label, active_tab_id, agent_status)
    ):
        return None
    if not isinstance(number, int) or isinstance(number, bool):
        return None
    if not isinstance(focused, bool):
        return None
    return HerdrWorkspace(
        workspace_id=workspace_id,
        number=number,
        label=label,
        active_tab_id=active_tab_id,
        agent_status=agent_status,
        focused=focused,
    )


def _parse_pane(raw: object) -> HerdrPane | None:
    if not isinstance(raw, dict):
        return None
    pane_id = raw.get("pane_id")
    workspace_id = raw.get("workspace_id")
    tab_id = raw.get("tab_id")
    if not all(isinstance(value, str) for value in (pane_id, workspace_id, tab_id)):
        return None
    agent = raw.get("agent")
    focused = raw.get("focused", False)
    if not isinstance(focused, bool):
        return None
    return HerdrPane(
        pane_id=pane_id,
        workspace_id=workspace_id,
        tab_id=tab_id,
        agent=agent if isinstance(agent, str) and agent else None,
        focused=focused,
    )


def _parse_layout(raw: object) -> tuple[str, str] | None:
    if not isinstance(raw, dict):
        return None
    tab_id = raw.get("tab_id")
    focused_pane_id = raw.get("focused_pane_id")
    if not isinstance(tab_id, str) or not isinstance(focused_pane_id, str):
        return None
    return tab_id, focused_pane_id


def parse_herdr_snapshot(raw: object) -> HerdrSnapshot:
    if not isinstance(raw, dict):
        return HerdrSnapshot.unavailable()
    workspaces_raw = raw.get("workspaces")
    panes_raw = raw.get("panes")
    layouts_raw = raw.get("layouts")
    if not isinstance(workspaces_raw, list) or not isinstance(panes_raw, list):
        return HerdrSnapshot.unavailable()
    layouts_raw = layouts_raw if isinstance(layouts_raw, list) else []
    workspaces = tuple(
        workspace
        for workspace in (_parse_workspace(item) for item in workspaces_raw)
        if workspace is not None
    )
    panes = tuple(
        pane for pane in (_parse_pane(item) for item in panes_raw) if pane is not None
    )
    focused: dict[str, str] = {}
    for item in layouts_raw:
        layout = _parse_layout(item)
        if layout is not None:
            focused[layout[0]] = layout[1]
    return HerdrSnapshot(
        available=True,
        workspaces=workspaces,
        panes=panes,
        focused_pane_ids=focused,
    )


def parse_topology_event(raw: object) -> str | None:
    if not isinstance(raw, dict):
        return None
    kind = raw.get("event")
    return kind if isinstance(kind, str) and kind in TOPOLOGY_EVENT_KINDS else None


class LiveHerdrAdapter:
    """Herdr socket adapter for snapshots and focus requests.

    The socket uses one JSON object per line with a string request id.
    """

    def __init__(self, socket_path: str | None = None) -> None:
        self.socket_path = resolve_herdr_socket_path(socket_path)
        self._subscribers: list[HerdrChangeCallback] = []
        self._subscriber_lock = Lock()
        self._subscription_thread: Thread | None = None

    def subscribe(self, on_change: HerdrChangeCallback) -> None:
        with self._subscriber_lock:
            if on_change not in self._subscribers:
                self._subscribers.append(on_change)
        self._ensure_subscription()

    def snapshot(self) -> HerdrSnapshot:
        try:
            response = self._request("session.snapshot", {})
        except (OSError, ValueError):
            return HerdrSnapshot.unavailable()
        result = response.get("result") if isinstance(response, dict) else None
        if not isinstance(result, dict) or result.get("type") != "session_snapshot":
            return HerdrSnapshot.unavailable()
        return parse_herdr_snapshot(result.get("snapshot"))

    def request_agent_focus(self, pane_id: str) -> bool:
        return self._request_action("agent.focus", {"target": pane_id})

    def request_workspace_focus(self, workspace_id: str) -> bool:
        return self._request_action(
            "workspace.focus", {"workspace_id": workspace_id}
        )

    def _request_action(self, method: str, params: dict[str, object]) -> bool:
        try:
            response = self._request(method, params)
        except (OSError, ValueError):
            return False
        return isinstance(response, dict) and isinstance(response.get("result"), dict)

    def _ensure_subscription(self) -> None:
        with self._subscriber_lock:
            if (
                self._subscription_thread is not None
                and self._subscription_thread.is_alive()
            ):
                return
            self._subscription_thread = Thread(
                target=self._subscription_loop,
                name="touchsignal-herdr-events",
                daemon=True,
            )
            self._subscription_thread.start()

    def _subscription_loop(self) -> None:
        while True:
            try:
                self._consume_subscription()
            except (OSError, ValueError):
                self._notify_subscribers()
            sleep(0.5)

    def _consume_subscription(self) -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(self.socket_path)
            request = json.dumps(
                {
                    "id": "touchsignal-events",
                    "method": "events.subscribe",
                    "params": {"subscriptions": HERDR_REFRESH_SUBSCRIPTIONS},
                }
            )
            client.sendall((request + "\n").encode("utf-8"))
            with client.makefile("rb") as stream:
                acknowledgement = self._decode_line(stream.readline())
                result = (
                    acknowledgement.get("result")
                    if isinstance(acknowledgement, dict)
                    else None
                )
                if (
                    not isinstance(result, dict)
                    or result.get("type") != "subscription_started"
                ):
                    raise ValueError("Herdr did not accept the event subscription")
                self._notify_subscribers()
                for line in stream:
                    event = self._decode_line(line)
                    if parse_topology_event(event) is not None:
                        self._notify_subscribers()

        raise ConnectionError("Herdr event subscription closed")

    def _notify_subscribers(self) -> None:
        with self._subscriber_lock:
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber()
            except Exception:
                continue

    @staticmethod
    def _decode_line(line: bytes) -> object:
        if not line:
            raise ValueError("Herdr closed the socket without a response")
        return json.loads(line.decode("utf-8"))

    def _request(self, method: str, params: dict[str, object]) -> dict[str, object]:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5.0)
            client.connect(self.socket_path)
            request = json.dumps({"id": method, "method": method, "params": params})
            client.sendall((request + "\n").encode("utf-8"))
            response = b""
            while b"\n" not in response:
                chunk = client.recv(65536)
                if not chunk:
                    break
                response += chunk
        line, _, _remainder = response.partition(b"\n")
        decoded = self._decode_line(line)
        if not isinstance(decoded, dict):
            raise ValueError("Herdr returned a non-object response")
        return decoded
