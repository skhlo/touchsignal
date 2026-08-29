from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

from touchbar_owner.cli import build_product_runtime
from touchbar_owner.chatgpt import (
    DesktopEntryResolver,
    HyprlandClient,
    HyprlandSnapshot,
    LiveHyprlandChatGPTAdapter,
    parse_active_address,
    parse_hyprland_clients,
)
from touchbar_owner.host import FakeHost
from touchbar_owner.runtime import SupervisedRuntime
from touchbar_owner.types import NATIVE_HEIGHT, NATIVE_WIDTH, TouchEvent
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


@dataclass
class FakeChatGPTActions:
    launches: int = 0
    focused: list[HyprlandClient] = field(default_factory=list)

    def request_launch(self) -> None:
        self.launches += 1

    def request_focus(self, client: HyprlandClient) -> None:
        self.focused.append(client)


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

    def test_focus_timeout_stays_unavailable_until_hyprland_verifies_focus(self) -> None:
        clock = ManualClock()
        source = FakeHyprlandSource(
            HyprlandSnapshot(available=True, clients=(CHATGPT_CLIENT,), active_address=None)
        )
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

        self.assertEqual(workflow.frame().chatgpt_tile.state, ChatGPTState.UNAVAILABLE)
        self.assertEqual(workflow.frame().chatgpt_tile.state, ChatGPTState.UNAVAILABLE)

        source.current = HyprlandSnapshot(
            available=True,
            clients=(CHATGPT_CLIENT,),
            active_address=CHATGPT_CLIENT.address,
        )

        self.assertEqual(workflow.frame().chatgpt_tile.state, ChatGPTState.FOCUSED)

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
    def test_live_snapshot_polling_is_bounded(self) -> None:
        clock = ManualClock()

        class CountingAdapter(LiveHyprlandChatGPTAdapter):
            def __init__(self) -> None:
                super().__init__(clock=clock)
                self.commands: list[tuple[str, ...]] = []

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
