from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from touchbar_owner.cli import build_product_runtime
from touchbar_owner.chatgpt import (
    DesktopEntryResolver,
    HyprlandClient,
    HyprlandSocketClient,
    HyprlandSnapshot,
    LiveHyprlandChatGPTAdapter,
    parse_active_address,
    parse_hyprland_event,
    parse_hyprland_clients,
)
from touchbar_owner.host import FakeHost
from touchbar_owner.herdr import HerdrSnapshot
from touchbar_owner.runtime import SupervisedRuntime
from touchbar_owner.system_layer import LockState, MediaLayerFrame
from touchbar_owner.thermal import (
    GpuRuntimeState,
    TemperatureReading,
    ThermalSnapshot,
)
from touchbar_owner.types import (
    NATIVE_HEIGHT,
    NATIVE_WIDTH,
    FnEvent,
    MediaAction,
    TouchEvent,
)
from touchbar_owner.workflow import TOUCH_SLOP, ChatGPTState, ChatGPTWorkflow, Geometry, WorkflowFrame, WorkflowRenderer


CHATGPT_CLIENT = HyprlandClient(address="0xabc", class_name="chatgpt")
EXPECTED_CHATGPT_TARGET_WIDTH = 112
EXPECTED_CHATGPT_TARGET_HEIGHT = 46
EXPECTED_VISUAL_BOX_SIZE = 30
EXPECTED_BUTTON_ONE_GLYPH = "󱚣"
EXPECTED_BUTTON_ONE_FONT = "monospace"


@dataclass
class FakeHyprlandSource:
    current: HyprlandSnapshot

    def snapshot(self) -> HyprlandSnapshot:
        return self.current


class FakeThermalSource:
    def snapshot(self) -> ThermalSnapshot:
        return ThermalSnapshot(
            cpu=TemperatureReading.measured(56),
            gpu_runtime=GpuRuntimeState.ACTIVE,
            gpu=TemperatureReading.measured(62),
        )


@dataclass
class FakeChatGPTActions:
    launches: int = 0
    focused: list[HyprlandClient] = field(default_factory=list)

    def request_launch(self) -> None:
        self.launches += 1

    def request_focus(self, client: HyprlandClient) -> None:
        self.focused.append(client)


@dataclass
class FakeLockSource:
    current: LockState = LockState.UNLOCKED

    def state(self) -> LockState:
        return self.current


@dataclass
class FakeHerdrSource:
    current: HerdrSnapshot = field(default_factory=HerdrSnapshot.unavailable)

    def snapshot(self) -> HerdrSnapshot:
        return self.current

    def subscribe(self, _on_change) -> None:
        return None


class FakeHerdrActions:
    def request_agent_focus(self, _pane_id: str) -> bool:
        return False

    def request_workspace_focus(self, _workspace_id: str) -> bool:
        return False


class ManualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class ChatGPTWorkflowRenderTests(unittest.TestCase):
    def test_chatgpt_tile_keeps_left_edge_geometry_and_reserved_center(self) -> None:
        source = FakeHyprlandSource(HyprlandSnapshot(available=True))
        workflow = ChatGPTWorkflow(source, FakeChatGPTActions())

        frame = workflow.frame()

        self.assertEqual(frame.surface_size, (NATIVE_WIDTH, NATIVE_HEIGHT))
        self.assertEqual(
            frame.chatgpt_tile.target,
            Geometry(0, 7, EXPECTED_CHATGPT_TARGET_WIDTH, EXPECTED_CHATGPT_TARGET_HEIGHT),
        )
        self.assertEqual(frame.reserved_center.x, EXPECTED_CHATGPT_TARGET_WIDTH)
        self.assertEqual(frame.reserved_center.height, NATIVE_HEIGHT)
        self.assertIsNone(frame.chatgpt_tile.logo_asset)
        self.assertEqual(frame.chatgpt_tile.logo_glyph, EXPECTED_BUTTON_ONE_GLYPH)
        self.assertEqual(
            frame.chatgpt_tile.logo_font_family,
            EXPECTED_BUTTON_ONE_FONT,
        )
        self.assertEqual(frame.chatgpt_tile.logo_box.width, EXPECTED_VISUAL_BOX_SIZE)
        self.assertEqual(frame.chatgpt_tile.logo_box.height, EXPECTED_VISUAL_BOX_SIZE)
        self.assertEqual(frame.chatgpt_tile.status_box.width, EXPECTED_VISUAL_BOX_SIZE)
        self.assertEqual(frame.chatgpt_tile.status_box.height, EXPECTED_VISUAL_BOX_SIZE)
        self.assertEqual(frame.chatgpt_tile.logo_box.y, frame.chatgpt_tile.status_box.y)
        self.assertLess(frame.chatgpt_tile.logo_box.right, frame.chatgpt_tile.status_box.x)
        self.assertEqual(frame.chatgpt_tile.state, ChatGPTState.CLOSED)
        self.assertTrue(frame.safe_system_layer_available)

    def test_hyprland_snapshot_maps_only_truthful_chatgpt_window_states(self) -> None:
        source = FakeHyprlandSource(
            HyprlandSnapshot(available=True, clients=(CHATGPT_CLIENT,), active_address=None)
        )
        workflow = ChatGPTWorkflow(source, FakeChatGPTActions())
        self.assertEqual(workflow.frame().chatgpt_tile.state, ChatGPTState.OPEN)

        source.current = HyprlandSnapshot(
            available=True,
            clients=(CHATGPT_CLIENT,),
            active_address=CHATGPT_CLIENT.address,
        )
        self.assertEqual(workflow.frame().chatgpt_tile.state, ChatGPTState.FOCUSED)
        self.assertNotIn("Working", {state.value for state in ChatGPTState})

    def test_hyprland_loss_affects_only_chatgpt_capability(self) -> None:
        source = FakeHyprlandSource(HyprlandSnapshot.unavailable())
        workflow = ChatGPTWorkflow(source, FakeChatGPTActions())

        frame = workflow.frame()

        self.assertEqual(frame.chatgpt_tile.state, ChatGPTState.UNAVAILABLE)
        self.assertFalse(frame.chatgpt_tile.capability_available)
        self.assertTrue(frame.safe_system_layer_available)


class ChatGPTWorkflowActionTests(unittest.TestCase):
    def test_tapping_open_chatgpt_requests_focus_until_hyprland_verifies_it(self) -> None:
        source = FakeHyprlandSource(
            HyprlandSnapshot(available=True, clients=(CHATGPT_CLIENT,), active_address=None)
        )
        actions = FakeChatGPTActions()
        workflow = ChatGPTWorkflow(source, actions)

        workflow.process_touch_events([
            TouchEvent("down", 40, 20),
            TouchEvent("up", 40, 20),
        ])

        self.assertEqual(actions.focused, [CHATGPT_CLIENT])
        pending = workflow.frame()
        self.assertTrue(pending.chatgpt_tile.pending)
        self.assertEqual(pending.chatgpt_tile.state, ChatGPTState.OPEN)

        workflow.process_touch_events([
            TouchEvent("down", 40, 20),
            TouchEvent("up", 40, 20),
        ])
        self.assertEqual(actions.focused, [CHATGPT_CLIENT])

        source.current = HyprlandSnapshot(
            available=True,
            clients=(CHATGPT_CLIENT,),
            active_address=CHATGPT_CLIENT.address,
        )
        verified = workflow.frame()

        self.assertFalse(verified.chatgpt_tile.pending)
        self.assertEqual(verified.chatgpt_tile.state, ChatGPTState.FOCUSED)

    def test_tapping_closed_chatgpt_launches_desktop_entry_once_until_window_exists(self) -> None:
        source = FakeHyprlandSource(HyprlandSnapshot(available=True))
        actions = FakeChatGPTActions()
        workflow = ChatGPTWorkflow(source, actions)

        workflow.process_touch_events([
            TouchEvent("down", 40, 20),
            TouchEvent("up", 40, 20),
        ])
        workflow.process_touch_events([
            TouchEvent("down", 40, 20),
            TouchEvent("up", 40, 20),
        ])

        self.assertEqual(actions.launches, 1)
        self.assertEqual(workflow.frame().chatgpt_tile.state, ChatGPTState.OPENING)
        self.assertTrue(workflow.frame().chatgpt_tile.pending)

        source.current = HyprlandSnapshot(available=True, clients=(CHATGPT_CLIENT,), active_address=None)
        verified = workflow.frame()

        self.assertEqual(actions.launches, 1)
        self.assertFalse(verified.chatgpt_tile.pending)
        self.assertEqual(verified.chatgpt_tile.state, ChatGPTState.OPEN)

    def test_pending_timeout_produces_unavailable(self) -> None:
        clock = ManualClock()
        source = FakeHyprlandSource(HyprlandSnapshot(available=True))
        workflow = ChatGPTWorkflow(
            source,
            FakeChatGPTActions(),
            clock=clock,
            pending_timeout=2.0,
        )

        workflow.process_touch_events([
            TouchEvent("down", 40, 20),
            TouchEvent("up", 40, 20),
        ])
        clock.advance(2.0)

        frame = workflow.frame()

        self.assertFalse(frame.chatgpt_tile.pending)
        self.assertEqual(frame.chatgpt_tile.state, ChatGPTState.UNAVAILABLE)
        self.assertTrue(frame.safe_system_layer_available)

    def test_focus_timeout_recovers_to_open_and_allows_retry(self) -> None:
        clock = ManualClock()
        source = FakeHyprlandSource(
            HyprlandSnapshot(available=True, clients=(CHATGPT_CLIENT,), active_address=None)
        )
        actions = FakeChatGPTActions()
        workflow = ChatGPTWorkflow(
            source,
            actions,
            clock=clock,
            pending_timeout=2.0,
            failure_display_timeout=1.0,
        )

        workflow.process_touch_events([
            TouchEvent("down", 40, 20),
            TouchEvent("up", 40, 20),
        ])
        clock.advance(2.0)

        self.assertEqual(workflow.frame().chatgpt_tile.state, ChatGPTState.UNAVAILABLE)
        clock.advance(0.9)
        self.assertEqual(workflow.frame().chatgpt_tile.state, ChatGPTState.UNAVAILABLE)

        clock.advance(0.1)
        self.assertEqual(workflow.frame().chatgpt_tile.state, ChatGPTState.OPEN)

        workflow.process_touch_events([
            TouchEvent("down", 40, 20),
            TouchEvent("up", 40, 20),
        ])
        self.assertEqual(actions.focused, [CHATGPT_CLIENT, CHATGPT_CLIENT])

    def test_touch_down_drag_away_restore_and_release_inside_commit_once(self) -> None:
        source = FakeHyprlandSource(
            HyprlandSnapshot(available=True, clients=(CHATGPT_CLIENT,), active_address=None)
        )
        actions = FakeChatGPTActions()
        workflow = ChatGPTWorkflow(source, actions)

        workflow.process_touch_events([TouchEvent("down", 40, 20)])
        self.assertTrue(workflow.frame().chatgpt_tile.pressed)
        self.assertEqual(actions.focused, [])

        workflow.process_touch_events([TouchEvent("move", -TOUCH_SLOP - 1, 20)])
        self.assertTrue(workflow.frame().chatgpt_tile.touch_cancelled)

        workflow.process_touch_events([TouchEvent("move", -TOUCH_SLOP, 20)])
        self.assertTrue(workflow.frame().chatgpt_tile.pressed)

        workflow.process_touch_events([TouchEvent("up", -TOUCH_SLOP, 20)])

        self.assertEqual(actions.focused, [CHATGPT_CLIENT])

    def test_release_outside_forgiving_boundary_cancels_without_action(self) -> None:
        source = FakeHyprlandSource(
            HyprlandSnapshot(available=True, clients=(CHATGPT_CLIENT,), active_address=None)
        )
        actions = FakeChatGPTActions()
        workflow = ChatGPTWorkflow(source, actions)

        workflow.process_touch_events([
            TouchEvent("down", 40, 20),
            TouchEvent("move", 200, 20),
            TouchEvent("up", 200, 20),
        ])

        self.assertEqual(actions.focused, [])
        self.assertEqual(actions.launches, 0)


class ChatGPTRuntimeWorkflowTests(unittest.TestCase):
    def test_locked_product_runtime_rejects_agent_tap_and_contains_no_agent_frame(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw))
            host.queued_touch_events = [
                TouchEvent("down", 40, 20),
                TouchEvent("up", 40, 20),
            ]
            source = FakeHyprlandSource(
                HyprlandSnapshot(
                    available=True,
                    clients=(CHATGPT_CLIENT,),
                    active_address=None,
                )
            )
            actions = FakeChatGPTActions()
            runtime = build_product_runtime(
                host,
                restart_delay=0.0,
                source=source,
                actions=actions,
                herdr_source=FakeHerdrSource(),
                herdr_actions=FakeHerdrActions(),
                thermal_source=FakeThermalSource(),
                lock_source=FakeLockSource(LockState.LOCKED),
            )

            runtime.start()
            try:
                runtime.process_once()
            finally:
                runtime.stop()

        layer = host.presented_frames[-1].workflow_frame
        self.assertIsInstance(layer, MediaLayerFrame)
        self.assertFalse(hasattr(layer, "chatgpt_tile"))
        self.assertFalse(hasattr(layer, "cpu_temperature"))
        self.assertFalse(hasattr(layer, "gpu_temperature"))
        self.assertEqual(actions.focused, [])

    def test_product_runtime_presents_workflow_frame_from_injected_sources(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw))
            host.queued_touch_events = [
                TouchEvent("down", 40, 20),
                TouchEvent("up", 40, 20),
            ]
            source = FakeHyprlandSource(
                HyprlandSnapshot(available=True, clients=(CHATGPT_CLIENT,), active_address=None)
            )
            actions = FakeChatGPTActions()
            runtime = build_product_runtime(
                host,
                restart_delay=0.0,
                source=source,
                actions=actions,
                herdr_source=FakeHerdrSource(),
                herdr_actions=FakeHerdrActions(),
                thermal_source=FakeThermalSource(),
                lock_source=FakeLockSource(),
            )

            runtime.start()
            try:
                runtime.process_once()
            finally:
                runtime.stop()

        presented = host.presented_frames[-1]
        self.assertIsInstance(presented.workflow_frame, WorkflowFrame)
        self.assertEqual(actions.focused, [CHATGPT_CLIENT])
        assert isinstance(presented.workflow_frame, WorkflowFrame)
        self.assertEqual(
            presented.workflow_frame.chatgpt_tile.target.width,
            EXPECTED_CHATGPT_TARGET_WIDTH,
        )
        self.assertEqual(presented.workflow_frame.cpu_temperature.value, "56°C")
        self.assertEqual(presented.workflow_frame.gpu_temperature.value, "62°C")

    def test_fn_media_survives_agent_source_loss_and_release_restores_agents(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw))
            host.queued_fn_events.append(FnEvent("press"))
            host.queued_touch_events = [
                TouchEvent("down", 40, 20),
                TouchEvent("up", 40, 20),
            ]
            runtime = build_product_runtime(
                host,
                restart_delay=0.0,
                source=FakeHyprlandSource(HyprlandSnapshot.unavailable()),
                actions=FakeChatGPTActions(),
                herdr_source=FakeHerdrSource(),
                herdr_actions=FakeHerdrActions(),
                lock_source=FakeLockSource(),
            )

            runtime.start()
            try:
                runtime.process_once()
                media = runtime.state.frames[-1].workflow_frame
                self.assertIsInstance(media, MediaLayerFrame)
                self.assertEqual(
                    host.emitted_media_actions,
                    [MediaAction.BRIGHTNESS_DOWN],
                )

                host.queued_fn_events.append(FnEvent("release"))
                runtime.process_once()
                agents = runtime.state.frames[-1].workflow_frame
            finally:
                runtime.stop()

        self.assertIsInstance(agents, WorkflowFrame)
        assert isinstance(agents, WorkflowFrame)
        self.assertEqual(agents.chatgpt_tile.state, ChatGPTState.UNAVAILABLE)
        self.assertEqual(agents.herdr_tiles, ())

    def test_runtime_can_record_a_workflow_frame_from_injected_sources(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw))
            host.queued_touch_events = [
                TouchEvent("down", 40, 20),
                TouchEvent("up", 40, 20),
            ]
            source = FakeHyprlandSource(
                HyprlandSnapshot(available=True, clients=(CHATGPT_CLIENT,), active_address=None)
            )
            actions = FakeChatGPTActions()
            workflow = ChatGPTWorkflow(source, actions)
            runtime = SupervisedRuntime(host, renderer=WorkflowRenderer(workflow))

            runtime.start()
            try:
                runtime.process_once()
            finally:
                runtime.stop()

        workflow_frame = runtime.state.frames[-1].workflow_frame
        self.assertIsInstance(workflow_frame, WorkflowFrame)
        self.assertEqual(actions.focused, [CHATGPT_CLIENT])
        assert isinstance(workflow_frame, WorkflowFrame)
        self.assertEqual(workflow_frame.chatgpt_tile.target.width, EXPECTED_CHATGPT_TARGET_WIDTH)


class ChatGPTAdapterParsingTests(unittest.TestCase):
    def test_repeated_snapshot_keeps_one_hyprland_event_thread(self) -> None:
        threads = []

        class FakeThread:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs
                self.started = False
                threads.append(self)

            def is_alive(self) -> bool:
                return self.started

            def start(self) -> None:
                self.started = True

        class EmptyAdapter(LiveHyprlandChatGPTAdapter):
            def _json(self, command: list[str]) -> object:
                return [] if command[-1] == "clients" else {}

        with patch("touchbar_owner.chatgpt.Thread", FakeThread):
            adapter = EmptyAdapter()
            adapter.snapshot()
            adapter.snapshot()

        self.assertEqual(len(threads), 1)

    def test_window_events_invalidate_the_snapshot_without_waiting_for_poll(self) -> None:
        clock = ManualClock()

        class CountingAdapter(LiveHyprlandChatGPTAdapter):
            def __init__(self) -> None:
                super().__init__(clock=clock)
                self.commands: list[tuple[str, ...]] = []

            def _ensure_event_subscription(self) -> None:
                return None

            def _json(self, command: list[str]) -> object:
                self.commands.append(tuple(command))
                return [] if command[-1] == "clients" else {}

        adapter = CountingAdapter()
        self.assertTrue(adapter.snapshot().available)
        self.assertEqual(len(adapter.commands), 2)

        self.assertIsNone(parse_hyprland_event(b"workspace>>2\n"))
        self.assertFalse(adapter._handle_event_line(b"workspace>>2\n"))
        self.assertTrue(adapter.snapshot().available)
        self.assertEqual(len(adapter.commands), 2)

        self.assertEqual(
            parse_hyprland_event(b"activewindowv2>>0xabc\n"),
            "activewindowv2",
        )
        self.assertTrue(adapter._handle_event_line(b"activewindowv2>>0xabc\n"))
        self.assertTrue(adapter.snapshot().available)
        self.assertEqual(len(adapter.commands), 4)

    def test_live_event_socket_uses_session_path_and_invalidates_on_window_event(self) -> None:
        class FakeStream:
            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

            def __iter__(self):
                return iter(
                    (
                        b"workspace>>2\n",
                        b"openwindow>>abc,2,chatgpt,ChatGPT\n",
                    )
                )

        class FakeSocket:
            def __init__(self) -> None:
                self.connected: str | None = None
                self.closed = False

            def connect(self, path: str) -> None:
                self.connected = path

            def makefile(self, _mode: str) -> FakeStream:
                return FakeStream()

            def close(self) -> None:
                self.closed = True

        connection = FakeSocket()
        adapter = LiveHyprlandChatGPTAdapter(
            environ={
                "XDG_RUNTIME_DIR": "/run/user/1000",
                "HYPRLAND_INSTANCE_SIGNATURE": "instance",
            },
            event_socket_factory=lambda: connection,
        )
        adapter._snapshot_at = 1.0

        with self.assertRaises(ConnectionError):
            adapter._consume_events()

        self.assertEqual(
            connection.connected,
            "/run/user/1000/hypr/instance/.socket2.sock",
        )
        self.assertIsNone(adapter._snapshot_at)
        self.assertTrue(connection.closed)

    def test_event_socket_failures_reconnect_once_with_capped_backoff(self) -> None:
        delays: list[float] = []

        class StopLoop(Exception):
            pass

        def wait(delay: float) -> None:
            delays.append(delay)
            if len(delays) == 7:
                raise StopLoop

        adapter = LiveHyprlandChatGPTAdapter(
            sleep=wait,
            reconnect_initial=0.5,
            reconnect_max=8.0,
        )

        def fail_events() -> None:
            raise ConnectionError("Hyprland is unavailable")

        adapter._consume_events = fail_events

        with self.assertRaises(StopLoop):
            adapter._event_loop()

        self.assertEqual(delays, [0.5, 1.0, 2.0, 4.0, 8.0, 8.0, 8.0])

    def test_hyprland_socket_client_closes_after_one_json_response(self) -> None:
        class FakeSocket:
            def __init__(self) -> None:
                self.connected: str | None = None
                self.sent = b""
                self.closed = False

            def settimeout(self, _seconds: float) -> None:
                return None

            def connect(self, path: str) -> None:
                self.connected = path

            def sendall(self, payload: bytes) -> None:
                self.sent = payload

            def recv(self, _size: int) -> bytes:
                return b'[{"address":"0x1","class":"chatgpt"}]'

            def close(self) -> None:
                self.closed = True

        connection = FakeSocket()
        client = HyprlandSocketClient(
            environ={
                "XDG_RUNTIME_DIR": "/run/user/1000",
                "HYPRLAND_INSTANCE_SIGNATURE": "instance",
            },
            socket_factory=lambda: connection,
        )

        response = client.request("j/clients")

        self.assertEqual(response, '[{"address":"0x1","class":"chatgpt"}]')
        self.assertEqual(
            connection.connected,
            "/run/user/1000/hypr/instance/.socket.sock",
        )
        self.assertEqual(connection.sent, b"j/clients")
        self.assertTrue(connection.closed)

    def test_live_snapshot_polling_is_bounded(self) -> None:
        clock = ManualClock()

        class CountingAdapter(LiveHyprlandChatGPTAdapter):
            def __init__(self) -> None:
                super().__init__(clock=clock)
                self.commands: list[tuple[str, ...]] = []

            def _ensure_event_subscription(self) -> None:
                return None

            def _json(self, command: list[str]) -> object:
                self.commands.append(tuple(command))
                return [] if command[-1] == "clients" else {}

        adapter = CountingAdapter()

        for _ in range(20):
            self.assertTrue(adapter.snapshot().available)
        self.assertEqual(len(adapter.commands), 2)

        clock.advance(1.0)
        self.assertTrue(adapter.snapshot().available)
        self.assertEqual(len(adapter.commands), 2)

        clock.advance(1.0)
        self.assertTrue(adapter.snapshot().available)
        self.assertEqual(len(adapter.commands), 2)

        clock.advance(1.0)
        self.assertTrue(adapter.snapshot().available)
        self.assertEqual(len(adapter.commands), 2)

        clock.advance(1.0)
        self.assertTrue(adapter.snapshot().available)
        self.assertEqual(len(adapter.commands), 4)

    def test_hyprland_json_parsers_ignore_unusable_client_records(self) -> None:
        clients = parse_hyprland_clients([
            {"address": "0x1", "class": "chatgpt"},
            {"address": "0x2", "class": 9},
            {"class": "chatgpt"},
        ])

        self.assertEqual(clients, (HyprlandClient("0x1", "chatgpt"),))
        self.assertEqual(parse_active_address({"address": "0x1"}), "0x1")
        self.assertIsNone(parse_active_address({}))

    def test_desktop_entry_resolver_selects_chatgpt_by_startup_wm_class(self) -> None:
        with TemporaryDirectory() as raw:
            apps = Path(raw)
            (apps / "chatgpt.desktop").write_text(
                "\n".join(
                    [
                        "[Desktop Entry]",
                        "Name=ChatGPT",
                        "StartupWMClass=chatgpt",
                        "Exec=/opt/ChatGPT/chatgpt --new-window %U",
                    ]
                ),
                encoding="utf-8",
            )
            resolver = DesktopEntryResolver(search_dirs=(apps,))

            entry = resolver.chatgpt_entry()

            self.assertEqual(entry.path.name, "chatgpt.desktop")
            self.assertEqual(entry.launch_command(), ["/opt/ChatGPT/chatgpt", "--new-window"])


if __name__ == "__main__":
    unittest.main()
