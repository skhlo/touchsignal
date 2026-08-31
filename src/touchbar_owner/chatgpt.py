from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, Thread
from time import monotonic, sleep as default_sleep
from typing import Protocol

from .safeguards import CappedReconnectBackoff


CHATGPT_HYPRLAND_CLASS = "chatgpt"
HYPRLAND_REFRESH_EVENT_KINDS = frozenset(
    {"activewindowv2", "openwindow", "closewindow"}
)
HYPRLAND_RECONNECT_INITIAL = 0.5
HYPRLAND_RECONNECT_MAX = 30.0
HYPRLAND_RECONNECT_STABLE_AFTER = 10.0


@dataclass(frozen=True)
class HyprlandClient:
    address: str
    class_name: str

    @property
    def is_chatgpt(self) -> bool:
        return self.class_name.casefold() == CHATGPT_HYPRLAND_CLASS


@dataclass(frozen=True)
class HyprlandSnapshot:
    available: bool
    clients: tuple[HyprlandClient, ...] = ()
    active_address: str | None = None

    @classmethod
    def unavailable(cls) -> HyprlandSnapshot:
        return cls(available=False)

    @property
    def chatgpt_clients(self) -> tuple[HyprlandClient, ...]:
        return tuple(client for client in self.clients if client.is_chatgpt)

    @property
    def focused_chatgpt(self) -> HyprlandClient | None:
        if self.active_address is None:
            return None
        for client in self.chatgpt_clients:
            if client.address == self.active_address:
                return client
        return None


class HyprlandSource(Protocol):
    def snapshot(self) -> HyprlandSnapshot: ...


class ChatGPTActions(Protocol):
    def request_focus(self, client: HyprlandClient) -> None: ...
    def request_launch(self) -> None: ...


class UnixSocket(Protocol):
    def settimeout(self, seconds: float) -> None: ...
    def connect(self, path: str) -> None: ...
    def sendall(self, payload: bytes) -> None: ...
    def recv(self, size: int) -> bytes: ...
    def close(self) -> None: ...


def _unix_socket() -> UnixSocket:
    return socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)


class HyprlandSocketClient:
    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        socket_factory: Callable[[], UnixSocket] = _unix_socket,
    ) -> None:
        self.environ = os.environ if environ is None else environ
        self.socket_factory = socket_factory

    def request(self, command: str) -> str:
        runtime_dir = self.environ.get("XDG_RUNTIME_DIR")
        signature = self.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        if not runtime_dir or not signature:
            raise OSError("Hyprland IPC environment is unavailable")
        path = Path(runtime_dir) / "hypr" / signature / ".socket.sock"
        client = self.socket_factory()
        try:
            client.settimeout(2.0)
            client.connect(str(path))
            client.sendall(command.encode("utf-8"))
            response = client.recv(4 * 1024 * 1024)
        finally:
            client.close()
        if not response:
            raise OSError("Hyprland IPC returned no response")
        return response.decode("utf-8")


class HyprlandEventStream(Protocol):
    def __enter__(self) -> HyprlandEventStream: ...
    def __exit__(self, *_args) -> None: ...
    def __iter__(self) -> Iterator[bytes]: ...


class HyprlandEventSocket(Protocol):
    def connect(self, path: str) -> None: ...
    def makefile(self, mode: str) -> HyprlandEventStream: ...
    def close(self) -> None: ...


def _hyprland_event_socket() -> HyprlandEventSocket:
    return socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)


def parse_hyprland_clients(raw: object) -> tuple[HyprlandClient, ...]:
    if not isinstance(raw, list):
        return ()
    clients: list[HyprlandClient] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        address = item.get("address")
        class_name = item.get("class")
        if isinstance(address, str) and isinstance(class_name, str):
            clients.append(HyprlandClient(address=address, class_name=class_name))
    return tuple(clients)


def parse_active_address(raw: object) -> str | None:
    if not isinstance(raw, dict):
        return None
    address = raw.get("address")
    return address if isinstance(address, str) and address else None


def parse_hyprland_event(line: bytes) -> str | None:
    try:
        raw = line.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    kind, separator, _payload = raw.partition(">>")
    if not separator or kind not in HYPRLAND_REFRESH_EVENT_KINDS:
        return None
    return kind


def resolve_hyprland_event_socket_path(environ: Mapping[str, str]) -> str:
    runtime_dir = environ.get("XDG_RUNTIME_DIR")
    signature = environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not runtime_dir or not signature:
        raise OSError("Hyprland IPC environment is unavailable")
    return str(Path(runtime_dir) / "hypr" / signature / ".socket2.sock")


class LiveHyprlandChatGPTAdapter:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = monotonic,
        snapshot_interval: float = 4.0,
        ipc: HyprlandSocketClient | None = None,
        environ: Mapping[str, str] | None = None,
        event_socket_factory: Callable[[], HyprlandEventSocket] = _hyprland_event_socket,
        sleep: Callable[[float], None] = default_sleep,
        reconnect_initial: float = HYPRLAND_RECONNECT_INITIAL,
        reconnect_max: float = HYPRLAND_RECONNECT_MAX,
        reconnect_stable_after: float = HYPRLAND_RECONNECT_STABLE_AFTER,
    ) -> None:
        self.clock = clock
        self.snapshot_interval = snapshot_interval
        self.environ = os.environ if environ is None else environ
        self.ipc = ipc or HyprlandSocketClient(environ=self.environ)
        self.event_socket_factory = event_socket_factory
        self.sleep = sleep
        self._reconnect_backoff = CappedReconnectBackoff(
            initial=reconnect_initial,
            maximum=reconnect_max,
            stable_after=reconnect_stable_after,
        )
        self._cached_snapshot = HyprlandSnapshot.unavailable()
        self._snapshot_at: float | None = None
        self._snapshot_lock = Lock()
        self._event_thread_lock = Lock()
        self._event_thread: Thread | None = None
        self._event_started_at: float | None = None

    def snapshot(self) -> HyprlandSnapshot:
        self._ensure_event_subscription()
        with self._snapshot_lock:
            now = self.clock()
            if (
                self._snapshot_at is not None
                and now >= self._snapshot_at
                and now - self._snapshot_at < self.snapshot_interval
            ):
                return self._cached_snapshot
            try:
                clients = self._json(["hyprctl", "-j", "clients"])
                active = self._json(["hyprctl", "-j", "activewindow"])
            except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
                snapshot = HyprlandSnapshot.unavailable()
            else:
                snapshot = HyprlandSnapshot(
                    available=True,
                    clients=parse_hyprland_clients(clients),
                    active_address=parse_active_address(active),
                )
            self._cached_snapshot = snapshot
            self._snapshot_at = self.clock()
            return snapshot

    def _ensure_event_subscription(self) -> None:
        with self._event_thread_lock:
            if self._event_thread is not None and self._event_thread.is_alive():
                return
            self._event_thread = Thread(
                target=self._event_loop,
                name="touchsignal-hyprland-events",
                daemon=True,
            )
            self._event_thread.start()

    def _handle_event_line(self, line: bytes) -> bool:
        if parse_hyprland_event(line) is None:
            return False
        self._invalidate_snapshot()
        return True

    def _invalidate_snapshot(self) -> None:
        with self._snapshot_lock:
            self._snapshot_at = None

    def _event_loop(self) -> None:
        while True:
            self._event_started_at = None
            try:
                self._consume_events()
            except (OSError, ValueError):
                self._invalidate_snapshot()
            connected_for = (
                None
                if self._event_started_at is None
                else max(0.0, self.clock() - self._event_started_at)
            )
            self.sleep(self._reconnect_backoff.next_delay(connected_for))

    def _consume_events(self) -> None:
        client = self.event_socket_factory()
        try:
            client.connect(resolve_hyprland_event_socket_path(self.environ))
            self._event_started_at = self.clock()
            self._invalidate_snapshot()
            with client.makefile("rb") as stream:
                for line in stream:
                    self._handle_event_line(line)
        finally:
            client.close()
        raise ConnectionError("Hyprland event socket closed")

    def fresh_snapshot(self) -> HyprlandSnapshot:
        self._invalidate_snapshot()
        return self.snapshot()

    def request_focus(self, client: HyprlandClient) -> None:
        try:
            self.ipc.request(f"dispatch focuswindow address:{client.address}")
        except OSError:
            subprocess.run(
                ["hyprctl", "dispatch", "focuswindow", f"address:{client.address}"],
                check=False,
            )

    def request_launch(self) -> None:
        entry = DesktopEntryResolver().chatgpt_entry()
        command = entry.launch_command()
        subprocess.Popen(command)

    def _json(self, command: list[str]) -> object:
        try:
            response = self.ipc.request(f"j/{command[-1]}")
        except OSError:
            completed = subprocess.run(
                command,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            response = completed.stdout
        return json.loads(response)


@dataclass(frozen=True)
class DesktopEntry:
    path: Path
    name: str
    startup_wm_class: str
    exec_line: str

    def launch_command(self) -> list[str]:
        parts = shlex.split(self.exec_line)
        command = [part for part in parts if not _desktop_field_code(part)]
        if not command:
            raise RuntimeError(f"{self.path} has no executable Exec line")
        return command


class DesktopEntryResolver:
    def __init__(self, search_dirs: tuple[Path, ...] | None = None) -> None:
        self.search_dirs = search_dirs or self._default_dirs()

    def chatgpt_entry(self) -> DesktopEntry:
        entries = list(self._entries())
        for entry in entries:
            if entry.startup_wm_class.casefold() == CHATGPT_HYPRLAND_CLASS:
                return entry
        for entry in entries:
            name = entry.name.casefold()
            if "chatgpt" in name:
                return entry
        raise RuntimeError("ChatGPT desktop entry was not found")

    def _entries(self) -> tuple[DesktopEntry, ...]:
        entries: list[DesktopEntry] = []
        for directory in self.search_dirs:
            if not directory.exists():
                continue
            for path in sorted(directory.glob("*.desktop")):
                entry = _read_desktop_entry(path)
                if entry is not None:
                    entries.append(entry)
        return tuple(entries)

    def _default_dirs(self) -> tuple[Path, ...]:
        home = Path.home()
        data_home = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share"))
        raw_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")
        dirs = [data_home / "applications"]
        dirs.extend(Path(raw) / "applications" for raw in raw_dirs.split(":") if raw)
        return tuple(dirs)


def _read_desktop_entry(path: Path) -> DesktopEntry | None:
    values: dict[str, str] = {}
    in_desktop_entry = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            in_desktop_entry = stripped == "[Desktop Entry]"
            continue
        if not in_desktop_entry or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key] = value
    exec_line = values.get("Exec", "")
    if not exec_line:
        return None
    return DesktopEntry(
        path=path,
        name=values.get("Name", path.stem),
        startup_wm_class=values.get("StartupWMClass", ""),
        exec_line=exec_line,
    )


def _desktop_field_code(part: str) -> bool:
    return len(part) == 2 and part.startswith("%") and part[1].isalpha()
