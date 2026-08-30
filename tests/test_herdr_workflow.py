from __future__ import annotations

import sys
import unittest
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from unittest.mock import patch

from touchbar_owner.chatgpt import HyprlandClient, HyprlandSnapshot
from touchbar_owner.drm import (
    HERDR_STATE_COLORS,
    DARK_INK,
    LIGHT_INK,
    PANEL_BACKGROUND,
    STATUS_SIGN_COLORS,
    TILE_ATTENTION_BACKGROUND,
    TILE_BACKGROUND,
    _LOGO_MASK_CACHE,
    _draw_logo_asset,
    _tile_contrast,
    draw_runtime_frame,
)
from touchbar_owner.herdr import (
    HERDR_REFRESH_SUBSCRIPTIONS,
    HerdrPane,
    HerdrSnapshot,
    HerdrWorkspace,
    resolve_herdr_socket_path,
)
from touchbar_owner.types import NATIVE_HEIGHT, NATIVE_WIDTH, RuntimeFrame, TouchEvent
from touchbar_owner.workflow import (
    CHATGPT_TARGET_WIDTH,
    HERDR_SLOT_COUNT,
    HERDR_TARGET_HEIGHT,
    HERDR_TARGET_WIDTH,
    TOUCH_SLOP,
    VISUAL_BOX_SIZE,
    ChatGPTWorkflow,
    Geometry,
    HerdrState,
    HerdrWorkflow,
    WorkflowFrame,
    chatgpt_target_geometry,
    herdr_slot_geometry,
)


class ManualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class FakeHerdrSource:
    current: HerdrSnapshot
    snapshot_count: int = 0
    subscribers: list[Callable[[], None]] = field(default_factory=list)

    def snapshot(self) -> HerdrSnapshot:
        self.snapshot_count += 1
        return self.current

    def subscribe(self, on_change: Callable[[], None]) -> None:
        self.subscribers.append(on_change)

    def publish_change(self) -> None:
        for subscriber in self.subscribers:
            subscriber()


@dataclass
class FakeHerdrActions:
    agent_focuses: list[str] = field(default_factory=list)
    workspace_focuses: list[str] = field(default_factory=list)
    accepts_focus: bool = True

    def request_agent_focus(self, pane_id: str) -> bool:
        self.agent_focuses.append(pane_id)
        return self.accepts_focus

    def request_workspace_focus(self, workspace_id: str) -> bool:
        self.workspace_focuses.append(workspace_id)
        return self.accepts_focus


@dataclass
class FakeHyprlandSource:
    current: HyprlandSnapshot

    def snapshot(self) -> HyprlandSnapshot:
        return self.current


@dataclass
class FakeChatGPTActions:
    focused: list[HyprlandClient] = field(default_factory=list)

    def request_launch(self) -> None:
        return None

    def request_focus(self, client: HyprlandClient) -> None:
        self.focused.append(client)


@dataclass(frozen=True)
class FakeTextExtents:
    width: float
    height: float = 10.0
    x_bearing: float = 0.0
    y_bearing: float = 0.0


@dataclass
class RecordingContext:
    rectangles: list[tuple[int, int, int, int]] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    colors: list[tuple[float, float, float]] = field(default_factory=list)
    masks: list[tuple[object, int, int]] = field(default_factory=list)

    def set_source_rgb(self, red: float, green: float, blue: float) -> None:
        self.colors.append((red, green, blue))

    def rectangle(self, x: int, y: int, width: int, height: int) -> None:
        self.rectangles.append((x, y, width, height))

    def text_extents(self, text: str) -> FakeTextExtents:
        return FakeTextExtents(width=float(len(text) * 8))

    def show_text(self, text: str) -> None:
        self.texts.append(text)

    def mask_surface(self, surface: object, x: int, y: int) -> None:
        self.masks.append((surface, x, y))

    def __getattr__(self, _name: str) -> Callable[..., None]:
        return lambda *_args, **_kwargs: None


def make_workspace(
    number: int,
    *,
    status: str = "idle",
    agent: str | None = "codex",
    workspace_id: str | None = None,
    label: str = "workspace",
) -> tuple[HerdrWorkspace, HerdrPane | None]:
    wid = workspace_id or f"w{number}"
    tab_id = f"{wid}:t1"
    pane_id = f"{wid}:p1"
    workspace = HerdrWorkspace(
        workspace_id=wid,
        number=number,
        label=label,
        active_tab_id=tab_id,
        agent_status=status,
        focused=False,
    )
    pane = HerdrPane(
        pane_id=pane_id,
        workspace_id=wid,
        tab_id=tab_id,
        agent=agent,
        focused=False,
    )
    return workspace, pane


def make_snapshot(
    entries: list[tuple[HerdrWorkspace, HerdrPane | None]],
    *,
    available: bool = True,
) -> HerdrSnapshot:
    workspaces = tuple(workspace for workspace, _ in entries)
    panes = tuple(pane for _, pane in entries if pane is not None)
    focused = {
        workspace.active_tab_id: pane.pane_id
        for workspace, pane in entries
        if pane is not None
    }
    return HerdrSnapshot(
        available=available,
        workspaces=workspaces,
        panes=panes,
        focused_pane_ids=focused,
    )


def herdr_workflow(
    snapshot: HerdrSnapshot,
    *,
    clock: ManualClock | None = None,
) -> tuple[HerdrWorkflow, FakeHerdrSource, FakeHerdrActions]:
    source = FakeHerdrSource(snapshot)
    actions = FakeHerdrActions()
    workflow = HerdrWorkflow(
        source,
        actions,
        clock=clock or ManualClock(),
        pending_timeout=2.0,
    )
    return workflow, source, actions


class HerdrTileGeometryTests(unittest.TestCase):
    def test_zero_workspaces_preserve_four_empty_slots_after_chatgpt(self) -> None:
        workflow, _, _ = herdr_workflow(make_snapshot([]))

        frame = workflow.frame()

        self.assertIsInstance(frame, WorkflowFrame)
        self.assertEqual(frame.chatgpt_tile.target, chatgpt_target_geometry())
        self.assertEqual(frame.chatgpt_tile.target.x, 0)
        self.assertEqual(len(frame.herdr_tiles), 0)
        self.assertEqual(len(frame.empty_slots), HERDR_SLOT_COUNT)
        for index, empty in enumerate(frame.empty_slots):
            self.assertEqual(empty.target, herdr_slot_geometry(index))
        self.assertEqual(frame.reserved_center.x, CHATGPT_TARGET_WIDTH + HERDR_SLOT_COUNT * HERDR_TARGET_WIDTH)

    def test_one_through_four_workspaces_render_in_stable_numeric_order(self) -> None:
        entries = [make_workspace(3), make_workspace(1)]
        workflow, _, _ = herdr_workflow(make_snapshot(entries))
        frame = workflow.frame()
        self.assertEqual(
            [tile.workspace_number for tile in frame.herdr_tiles], [1, 3]
        )
        self.assertEqual(len(frame.empty_slots), 2)

        four = [make_workspace(4), make_workspace(2), make_workspace(1), make_workspace(3)]
        workflow, _, _ = herdr_workflow(make_snapshot(four))
        frame = workflow.frame()
        self.assertEqual(
            [tile.workspace_number for tile in frame.herdr_tiles], [1, 2, 3, 4]
        )
        self.assertEqual(len(frame.empty_slots), 0)

    def test_more_than_four_workspaces_selects_first_four_by_number(self) -> None:
        entries = [make_workspace(7), make_workspace(3), make_workspace(9), make_workspace(1), make_workspace(5)]
        workflow, _, _ = herdr_workflow(make_snapshot(entries))

        frame = workflow.frame()

        self.assertEqual(
            [tile.workspace_number for tile in frame.herdr_tiles], [1, 3, 5, 7]
        )

    def test_slot_geometry_is_stable_and_matches_hit_target(self) -> None:
        for index in range(HERDR_SLOT_COUNT):
            target = herdr_slot_geometry(index)
            self.assertEqual(target.width, 112)
            self.assertEqual(target.height, 46)
            self.assertEqual(target.x, CHATGPT_TARGET_WIDTH + index * HERDR_TARGET_WIDTH)
            self.assertEqual(target.y, (NATIVE_HEIGHT - HERDR_TARGET_HEIGHT) // 2)


class HerdrTilePresentationTests(unittest.TestCase):
    def test_repeated_agent_identities_remain_distinguishable_by_number(self) -> None:
        first, first_pane = make_workspace(2, agent="codex")
        second, second_pane = make_workspace(5, agent="codex")
        workflow, _, _ = herdr_workflow(
            make_snapshot([(first, first_pane), (second, second_pane)])
        )

        frame = workflow.frame()

        identities = [tile.agent_identity for tile in frame.herdr_tiles]
        self.assertEqual(identities, ["codex", "codex"])
        self.assertEqual(
            [tile.workspace_number for tile in frame.herdr_tiles], [2, 5]
        )
        self.assertEqual(
            [tile.logo_asset for tile in frame.herdr_tiles],
            ["assets/agents/codex-logo-white.svg", "assets/agents/codex-logo-white.svg"],
        )

    def test_every_lifecycle_state_maps_label_sign_and_token(self) -> None:
        expected = {
            "idle": (HerdrState.READY, "dot", "ready"),
            "working": (HerdrState.WORKING, "arrow", "working"),
            "blocked": (HerdrState.BLOCKED, "bang", "blocked"),
            "done": (HerdrState.DONE, "check", "done"),
            "unknown": (HerdrState.UNKNOWN, "question", "unknown"),
        }
        for status, (state, sign, token) in expected.items():
            workspace, _ = make_workspace(1, status=status)
            workflow, _, _ = herdr_workflow(make_snapshot([(workspace, None)]))

            tile = workflow.frame().herdr_tiles[0]

            self.assertEqual(tile.state, state, status)
            self.assertEqual(tile.state.value, state.value, status)
            self.assertEqual(tile.status_sign, sign, status)
            self.assertEqual(tile.state_token, token, status)

    def test_logo_and_status_boxes_share_equal_30_pixel_boxes(self) -> None:
        workspace, _ = make_workspace(1, agent="claude")
        workflow, _, _ = herdr_workflow(make_snapshot([(workspace, None)]))

        tile = workflow.frame().herdr_tiles[0]

        self.assertIsNotNone(tile.logo_box)
        self.assertIsNotNone(tile.status_box)
        assert tile.logo_box is not None and tile.status_box is not None
        self.assertEqual(tile.logo_box.width, VISUAL_BOX_SIZE)
        self.assertEqual(tile.logo_box.height, VISUAL_BOX_SIZE)
        self.assertEqual(tile.status_box.width, VISUAL_BOX_SIZE)
        self.assertEqual(tile.status_box.height, VISUAL_BOX_SIZE)
        self.assertLess(tile.logo_box.right, tile.status_box.x)

    def test_active_tab_focused_pane_identity_is_used(self) -> None:
        workspace, pane = make_workspace(1, agent="codex")
        workflow, _, _ = herdr_workflow(make_snapshot([(workspace, pane)]))

        tile = workflow.frame().herdr_tiles[0]

        self.assertEqual(tile.agent_identity, "codex")
        self.assertIsNotNone(tile.logo_asset)

    def test_workspace_aggregate_lifecycle_is_used_over_focused_pane(self) -> None:
        workspace, pane = make_workspace(1, status="blocked", agent="codex")
        workflow, _, _ = herdr_workflow(make_snapshot([(workspace, pane)]))

        tile = workflow.frame().herdr_tiles[0]

        self.assertEqual(tile.agent_identity, "codex")
        self.assertEqual(tile.state, HerdrState.BLOCKED)
        self.assertEqual(tile.status_sign, "bang")

    def test_source_loss_maps_populated_tiles_to_unavailable(self) -> None:
        workspace, pane = make_workspace(1, status="working", agent="codex")
        workflow, source, _ = herdr_workflow(make_snapshot([(workspace, pane)]))
        workflow.frame()

        source.current = HerdrSnapshot.unavailable()
        source.publish_change()
        tile = workflow.frame().herdr_tiles[0]

        self.assertTrue(tile.source_lost)
        self.assertEqual(tile.state, HerdrState.UNAVAILABLE)

    def test_runtime_drawing_shows_workspace_identity_and_unknown_sign(self) -> None:
        workspace, pane = make_workspace(1, status="unknown", agent="myagent")
        workflow, _, _ = herdr_workflow(make_snapshot([(workspace, pane)]))
        workflow_frame = workflow.frame()
        workflow_frame = replace(
            workflow_frame,
            chatgpt_tile=replace(workflow_frame.chatgpt_tile, logo_asset=None),
        )
        context = RecordingContext()

        draw_runtime_frame(
            context,
            NATIVE_WIDTH,
            NATIVE_HEIGHT,
            RuntimeFrame(
                surface_size=(NATIVE_WIDTH, NATIVE_HEIGHT),
                touch_count=0,
                last_touch=None,
                workflow_frame=workflow_frame,
            ),
        )

        tile = workflow_frame.herdr_tiles[0]
        self.assertIn(
            (tile.target.x, tile.target.y, tile.target.width, tile.target.height),
            context.rectangles,
        )
        self.assertEqual(
            context.rectangles.count(
                (tile.target.x, tile.target.y, tile.target.width, tile.target.height)
            ),
            1,
        )
        self.assertIn("1", context.texts)
        self.assertIn("MY", context.texts)
        self.assertIn("?", context.texts)
        self.assertIn(HERDR_STATE_COLORS["unknown"], context.colors)
        self.assertIn(PANEL_BACKGROUND, context.colors)
        self.assertIn(LIGHT_INK, context.colors)
        self.assertIn(DARK_INK, context.colors)
        empty = workflow_frame.empty_slots[0].target
        self.assertNotIn(
            (empty.x, empty.y, empty.width, empty.height),
            context.rectangles,
        )

    def test_fixed_palette_reserves_color_for_semantic_status(self) -> None:
        white = (1.0, 1.0, 1.0)
        blue = (0.20, 0.65, 1.0)
        amber = (1.0, 0.65, 0.0)
        green = (0.25, 0.90, 0.45)
        red = (1.0, 0.25, 0.30)

        self.assertEqual(LIGHT_INK, white)
        self.assertEqual(DARK_INK, PANEL_BACKGROUND)
        self.assertEqual(TILE_BACKGROUND, PANEL_BACKGROUND)
        self.assertEqual(TILE_ATTENTION_BACKGROUND, white)
        self.assertEqual(
            set(HERDR_STATE_COLORS.values()),
            {white, blue, amber, green, red},
        )
        self.assertEqual(
            set(STATUS_SIGN_COLORS.values()),
            {white, blue, amber, green, red},
        )
        self.assertEqual(
            _tile_contrast(attention=False, pressed=False, pending=False),
            (PANEL_BACKGROUND, LIGHT_INK),
        )
        self.assertEqual(
            _tile_contrast(attention=True, pressed=False, pending=False),
            (TILE_ATTENTION_BACKGROUND, DARK_INK),
        )

    def test_color_logo_assets_are_applied_as_black_alpha_masks(self) -> None:
        class FakeSurface:
            png_loads = 0

            def __init__(self, _format: object, width: int, height: int) -> None:
                self.width = width
                self.height = height

            @classmethod
            def create_from_png(cls, _path: str) -> FakeSurface:
                cls.png_loads += 1
                return cls("png", 512, 512)

            def get_width(self) -> int:
                return self.width

            def get_height(self) -> int:
                return self.height

            def flush(self) -> None:
                return None

        class FakeMaskContext:
            def __init__(self, _surface: FakeSurface) -> None:
                return None

            def __getattr__(self, _name: str) -> Callable[..., None]:
                return lambda *_args, **_kwargs: None

        class FakeCairo:
            FORMAT_ARGB32 = "argb32"
            ImageSurface = FakeSurface
            Context = FakeMaskContext

        context = RecordingContext()
        box = Geometry(10, 8, VISUAL_BOX_SIZE, VISUAL_BOX_SIZE)

        _LOGO_MASK_CACHE.clear()
        try:
            with patch.dict(sys.modules, {"cairo": FakeCairo()}):
                first = _draw_logo_asset(
                    context,
                    box,
                    "assets/agents/claude-logo-light.png",
                    DARK_INK,
                )
                second = _draw_logo_asset(
                    context,
                    box,
                    "assets/agents/claude-logo-light.png",
                    LIGHT_INK,
                )

            self.assertTrue(first)
            self.assertTrue(second)
            self.assertEqual(FakeSurface.png_loads, 1)
            self.assertEqual(context.colors[-2:], [DARK_INK, LIGHT_INK])
            self.assertEqual(len(context.masks), 2)
            self.assertEqual(context.masks[0][1:], (box.x, box.y))
        finally:
            _LOGO_MASK_CACHE.clear()


class HerdrFocusActionTests(unittest.TestCase):
    @staticmethod
    def tap_slot(slot: int) -> list[TouchEvent]:
        target = herdr_slot_geometry(slot)
        x = target.x + target.width // 2
        y = target.y + target.height // 2
        return [TouchEvent("down", x, y), TouchEvent("up", x, y)]

    def test_tap_with_agent_focuses_main_agent(self) -> None:
        workspace, pane = make_workspace(1, agent="codex")
        workflow, _, actions = herdr_workflow(make_snapshot([(workspace, pane)]))

        workflow.process_touch_events(self.tap_slot(0))

        self.assertEqual(actions.agent_focuses, [pane.pane_id])
        self.assertEqual(actions.workspace_focuses, [])
        self.assertTrue(workflow.frame().herdr_tiles[0].pending)

    def test_tap_without_agent_focuses_workspace(self) -> None:
        workspace, _ = make_workspace(1, agent=None)
        workflow, _, actions = herdr_workflow(make_snapshot([(workspace, None)]))

        workflow.process_touch_events(self.tap_slot(0))

        self.assertEqual(actions.agent_focuses, [])
        self.assertEqual(actions.workspace_focuses, [workspace.workspace_id])
        self.assertTrue(workflow.frame().herdr_tiles[0].pending)

    def test_drag_away_cancels_and_drag_back_restores_pressed_tile(self) -> None:
        workspace, pane = make_workspace(1, agent="codex")
        workflow, _, actions = herdr_workflow(make_snapshot([(workspace, pane)]))
        target = herdr_slot_geometry(0)

        workflow.process_touch_events(
            [TouchEvent("down", target.x + target.width // 2, target.y + 2)]
        )
        self.assertTrue(workflow.frame().herdr_tiles[0].pressed)

        workflow.process_touch_events(
            [TouchEvent("move", target.right + TOUCH_SLOP + 1, target.y + 2)]
        )
        cancelled = workflow.frame().herdr_tiles[0]
        self.assertFalse(cancelled.pressed)
        self.assertTrue(cancelled.touch_cancelled)

        workflow.process_touch_events(
            [TouchEvent("move", target.right + TOUCH_SLOP, target.y + 2)]
        )
        self.assertTrue(workflow.frame().herdr_tiles[0].pressed)

        workflow.process_touch_events(
            [TouchEvent("up", target.right + TOUCH_SLOP, target.y + 2)]
        )
        self.assertEqual(actions.agent_focuses, [pane.pane_id])

    def test_agent_focus_pending_clears_only_after_verification(self) -> None:
        workspace, pane = make_workspace(1, agent="codex")
        unfocused = make_snapshot([(workspace, pane)])
        focused_pane = HerdrPane(
            pane_id=pane.pane_id,
            workspace_id=workspace.workspace_id,
            tab_id=workspace.active_tab_id,
            agent="codex",
            focused=True,
        )
        focused = make_snapshot([(workspace, focused_pane)])
        workflow, source, actions = herdr_workflow(unfocused)

        workflow.process_touch_events(self.tap_slot(0))
        self.assertEqual(actions.agent_focuses, [pane.pane_id])
        self.assertTrue(workflow.frame().herdr_tiles[0].pending)

        source.current = focused
        source.publish_change()
        self.assertFalse(workflow.frame().herdr_tiles[0].pending)

    def test_workspace_focus_pending_clears_only_after_verification(self) -> None:
        workspace, _ = make_workspace(1, agent=None)
        unfocused = make_snapshot([(workspace, None)])
        focused_workspace = HerdrWorkspace(
            workspace_id=workspace.workspace_id,
            number=workspace.number,
            label=workspace.label,
            active_tab_id=workspace.active_tab_id,
            agent_status=workspace.agent_status,
            focused=True,
        )
        workflow, source, actions = herdr_workflow(unfocused)

        workflow.process_touch_events(self.tap_slot(0))
        self.assertEqual(actions.workspace_focuses, [workspace.workspace_id])
        self.assertTrue(workflow.frame().herdr_tiles[0].pending)

        source.current = make_snapshot([(focused_workspace, None)])
        source.publish_change()
        self.assertFalse(workflow.frame().herdr_tiles[0].pending)

    def test_rejected_focus_request_becomes_unavailable_without_raising(self) -> None:
        workspace, pane = make_workspace(1, agent="codex")
        source = FakeHerdrSource(make_snapshot([(workspace, pane)]))
        actions = FakeHerdrActions(accepts_focus=False)
        workflow = HerdrWorkflow(source, actions)

        workflow.process_touch_events(self.tap_slot(0))

        tile = workflow.frame().herdr_tiles[0]
        self.assertFalse(tile.pending)
        self.assertTrue(tile.focus_failed)
        self.assertEqual(tile.state, HerdrState.UNAVAILABLE)

    def test_focus_timeout_produces_explicit_unknown_state(self) -> None:
        clock = ManualClock()
        workspace, pane = make_workspace(1, agent="codex")
        workflow, _, _ = herdr_workflow(make_snapshot([(workspace, pane)]), clock=clock)

        workflow.process_touch_events(self.tap_slot(0))
        clock.advance(2.0)

        tile = workflow.frame().herdr_tiles[0]
        self.assertFalse(tile.pending)
        self.assertTrue(tile.focus_timeout)
        self.assertEqual(tile.state, HerdrState.UNAVAILABLE)
        self.assertEqual(tile.status_sign, "bang")
        self.assertEqual(tile.state_token, "unavailable")

    def test_unknown_agent_identity_gets_stable_fallback(self) -> None:
        workspace, pane = make_workspace(1, agent="myagent")
        workflow, _, _ = herdr_workflow(make_snapshot([(workspace, pane)]))

        tile = workflow.frame().herdr_tiles[0]

        self.assertEqual(tile.agent_identity, "myagent")
        self.assertIsNone(tile.logo_asset)
        self.assertEqual(tile.identity_fallback, "MY")

    def test_focus_pending_survives_source_loss_as_unavailable(self) -> None:
        workspace, pane = make_workspace(1, agent="codex")
        workflow, source, actions = herdr_workflow(make_snapshot([(workspace, pane)]))

        workflow.process_touch_events(self.tap_slot(0))
        self.assertEqual(actions.agent_focuses, [pane.pane_id])

        source.current = HerdrSnapshot.unavailable()
        source.publish_change()
        tile = workflow.frame().herdr_tiles[0]

        self.assertTrue(tile.source_lost)
        self.assertEqual(tile.state, HerdrState.UNAVAILABLE)


class HerdrTopologyRefreshTests(unittest.TestCase):
    def test_source_change_takes_fresh_authoritative_snapshot(self) -> None:
        first, first_pane = make_workspace(1, agent="codex")
        second, second_pane = make_workspace(2, agent="claude")
        workflow, source, actions = herdr_workflow(
            make_snapshot([(first, first_pane)])
        )

        workflow.frame()
        count_after_first = source.snapshot_count

        source.current = make_snapshot([(first, first_pane), (second, second_pane)])
        stale = workflow.frame()

        self.assertEqual(source.snapshot_count, count_after_first)
        self.assertEqual(
            [tile.workspace_number for tile in stale.herdr_tiles], [1]
        )

        source.publish_change()
        frame = workflow.frame()

        self.assertGreater(source.snapshot_count, count_after_first)
        self.assertEqual(
            [tile.workspace_number for tile in frame.herdr_tiles], [1, 2]
        )
        self.assertEqual(
            [tile.agent_identity for tile in frame.herdr_tiles], ["codex", "claude"]
        )

    def test_topology_event_kinds_cover_herdr_change_events(self) -> None:
        from touchbar_owner.herdr import TOPOLOGY_EVENT_KINDS, parse_topology_event

        for kind in (
            "workspace_created",
            "workspace_closed",
            "tab_created",
            "tab_closed",
            "pane_created",
            "pane_closed",
            "pane_focused",
            "pane_agent_detected",
            "pane_agent_status_changed",
            "workspace_updated",
            "layout_updated",
        ):
            self.assertEqual(parse_topology_event({"event": kind}), kind)
        self.assertIsNone(parse_topology_event({"event": "pane_output_changed"}))
        self.assertIsNone(parse_topology_event({"event": 9}))
        self.assertIn("pane_moved", TOPOLOGY_EVENT_KINDS)
        subscription_types = {
            subscription["type"] for subscription in HERDR_REFRESH_SUBSCRIPTIONS
        }
        self.assertIn("workspace.updated", subscription_types)
        self.assertNotIn("pane.agent_status_changed", subscription_types)


class CompositeWorkflowContactTests(unittest.TestCase):
    def test_contact_owner_receives_move_and_release_across_tile_boundaries(self) -> None:
        client = HyprlandClient(address="0xabc", class_name="chatgpt")
        actions = FakeChatGPTActions()
        chatgpt = ChatGPTWorkflow(
            FakeHyprlandSource(
                HyprlandSnapshot(
                    available=True,
                    clients=(client,),
                    active_address=None,
                )
            ),
            actions,
        )
        workflow = HerdrWorkflow(
            FakeHerdrSource(make_snapshot([])),
            FakeHerdrActions(),
            chatgpt=chatgpt,
        )

        workflow.process_touch_events(
            [
                TouchEvent("down", 40, 20),
                TouchEvent("move", 200, 20),
                TouchEvent("up", 200, 20),
            ]
        )

        tile = workflow.frame().chatgpt_tile
        self.assertFalse(tile.pressed)
        self.assertFalse(tile.touch_cancelled)
        self.assertEqual(actions.focused, [])


class HerdrSocketPathTests(unittest.TestCase):
    def test_socket_path_uses_explicit_environment_and_xdg_precedence(self) -> None:
        home = Path("/users/example")

        self.assertEqual(
            resolve_herdr_socket_path(
                "/run/herdr-explicit.sock",
                environ={"HERDR_SOCKET_PATH": "/run/herdr-env.sock"},
                home=home,
            ),
            "/run/herdr-explicit.sock",
        )
        self.assertEqual(
            resolve_herdr_socket_path(
                environ={"HERDR_SOCKET_PATH": "/run/herdr-env.sock"},
                home=home,
            ),
            "/run/herdr-env.sock",
        )
        self.assertEqual(
            resolve_herdr_socket_path(
                environ={"XDG_CONFIG_HOME": "/tmp/example-config"},
                home=home,
            ),
            "/tmp/example-config/herdr/herdr.sock",
        )
        self.assertEqual(
            resolve_herdr_socket_path(environ={}, home=home),
            "/users/example/.config/herdr/herdr.sock",
        )
