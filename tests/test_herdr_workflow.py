from __future__ import annotations

import unittest
from dataclasses import dataclass, field

from touchbar_owner.herdr import HerdrPane, HerdrSnapshot, HerdrWorkspace
from touchbar_owner.types import NATIVE_HEIGHT, NATIVE_WIDTH, TouchEvent
from touchbar_owner.workflow import (
    CHATGPT_TARGET_WIDTH,
    HERDR_SLOT_COUNT,
    HERDR_TARGET_HEIGHT,
    HERDR_TARGET_WIDTH,
    VISUAL_BOX_SIZE,
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

    def snapshot(self) -> HerdrSnapshot:
        self.snapshot_count += 1
        return self.current


@dataclass
class FakeHerdrActions:
    agent_focuses: list[str] = field(default_factory=list)
    workspace_focuses: list[str] = field(default_factory=list)

    def request_agent_focus(self, pane_id: str) -> None:
        self.agent_focuses.append(pane_id)

    def request_workspace_focus(self, workspace_id: str) -> None:
        self.workspace_focuses.append(workspace_id)


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

    def test_logo_and_status_boxes_share_equal_27_pixel_boxes(self) -> None:
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
        tile = workflow.frame().herdr_tiles[0]

        self.assertTrue(tile.source_lost)
        self.assertEqual(tile.state, HerdrState.UNAVAILABLE)


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
        self.assertFalse(workflow.frame().herdr_tiles[0].pending)

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
        tile = workflow.frame().herdr_tiles[0]

        self.assertTrue(tile.source_lost)
        self.assertEqual(tile.state, HerdrState.UNAVAILABLE)


class HerdrTopologyRefreshTests(unittest.TestCase):
    def test_refresh_takes_fresh_authoritative_snapshot(self) -> None:
        first, first_pane = make_workspace(1, agent="codex")
        second, second_pane = make_workspace(2, agent="claude")
        workflow, source, actions = herdr_workflow(
            make_snapshot([(first, first_pane)])
        )

        workflow.frame()
        count_after_first = source.snapshot_count

        source.current = make_snapshot([(first, first_pane), (second, second_pane)])
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
            "pane_agent_detected",
            "layout_updated",
        ):
            self.assertEqual(parse_topology_event({"event": kind}), kind)
        self.assertIsNone(parse_topology_event({"event": "pane_output_changed"}))
        self.assertIsNone(parse_topology_event({"event": 9}))
        self.assertIn("pane_moved", TOPOLOGY_EVENT_KINDS)
