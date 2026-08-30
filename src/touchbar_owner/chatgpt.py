from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Protocol


CHATGPT_HYPRLAND_CLASS = "chatgpt"


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


class LiveHyprlandChatGPTAdapter:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = monotonic,
        snapshot_interval: float = 4.0,
        ipc: HyprlandSocketClient | None = None,
    ) -> None:
        self.clock = clock
        self.snapshot_interval = snapshot_interval
        self.ipc = ipc or HyprlandSocketClient()
        self._cached_snapshot = HyprlandSnapshot.unavailable()
        self._snapshot_at: float | None = None

    def snapshot(self) -> HyprlandSnapshot:
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
