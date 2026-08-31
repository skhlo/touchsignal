from __future__ import annotations

import subprocess
import unittest

from touchbar_owner.system_layer import (
    LiveOmarchyLockSource,
    LockState,
    MediaLayerFrame,
    SystemLayerWorkflow,
)
from touchbar_owner.types import MediaAction, RuntimeInput, TouchEvent


class FakeLockSource:
    def __init__(self, state: LockState = LockState.UNLOCKED) -> None:
        self.current = state

    def state(self) -> LockState:
        return self.current


class ManualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeAgentWorkflow:
    def process_touch_events(self, _events) -> None:
        raise AssertionError("the agent layer must not run while Fn is held")

    def frame(self):
        raise AssertionError("the agent frame must not be read while Fn is held")

    def cancel_contacts(self) -> None:
        return None

    def refresh_verified_state(self) -> None:
        return None


class ContactAgentWorkflow:
    def __init__(self) -> None:
        self.events: list[TouchEvent] = []
        self.cancel_count = 0
        self.identity = "codex"
        self.refresh_count = 0

    def process_touch_events(self, events: list[TouchEvent]) -> None:
        self.events.extend(events)

    def frame(self) -> object:
        return {"agent_identity": self.identity}

    def cancel_contacts(self) -> None:
        self.cancel_count += 1

    def refresh_verified_state(self) -> None:
        self.refresh_count += 1
        self.identity = "fresh-agent"


class SystemLayerWorkflowTests(unittest.TestCase):
    def test_holding_fn_shows_seven_media_controls_without_escape_or_agents(self) -> None:
        workflow = SystemLayerWorkflow(
            FakeAgentWorkflow(),
            FakeLockSource(),
        )

        result = workflow.render(RuntimeInput(fn_held=True))

        layer = result.frame.workflow_frame
        self.assertIsInstance(layer, MediaLayerFrame)
        assert isinstance(layer, MediaLayerFrame)
        self.assertEqual(
            tuple(button.action for button in layer.buttons),
            (
                MediaAction.BRIGHTNESS_DOWN,
                MediaAction.BRIGHTNESS_UP,
                MediaAction.PREVIOUS,
                MediaAction.PLAY_PAUSE,
                MediaAction.NEXT,
                MediaAction.VOLUME_DOWN,
                MediaAction.VOLUME_UP,
            ),
        )
        self.assertFalse(any("escape" in button.action.value for button in layer.buttons))
        self.assertFalse(hasattr(layer, "chatgpt_tile"))
        self.assertEqual(result.intents, ())

    def test_lock_during_an_agent_press_cancels_contact_and_removes_agent_frame(self) -> None:
        agents = ContactAgentWorkflow()
        lock = FakeLockSource()
        workflow = SystemLayerWorkflow(agents, lock)
        workflow.render(
            RuntimeInput(touches=(TouchEvent("down", 40, 20),), fn_held=False)
        )

        lock.current = LockState.LOCKED
        result = workflow.render(RuntimeInput())

        self.assertEqual(agents.cancel_count, 1)
        self.assertIsInstance(result.frame.workflow_frame, MediaLayerFrame)
        self.assertNotIn("codex", repr(result.frame.workflow_frame))

    def test_every_media_action_commits_once_on_release_with_forgiving_boundary(self) -> None:
        for index, action in enumerate(MediaAction):
            with self.subTest(action=action):
                workflow = SystemLayerWorkflow(
                    FakeAgentWorkflow(),
                    FakeLockSource(),
                )
                initial = workflow.render(RuntimeInput(fn_held=True))
                layer = initial.frame.workflow_frame
                assert isinstance(layer, MediaLayerFrame)
                target = layer.buttons[index].target

                down = workflow.render(
                    RuntimeInput(
                        touches=(TouchEvent("down", target.x + 1, target.y + 1),),
                        fn_held=True,
                    )
                )
                released = workflow.render(
                    RuntimeInput(
                        touches=(
                            TouchEvent("up", target.right + 10, target.y + 1),
                        ),
                        fn_held=True,
                    )
                )
                duplicate = workflow.render(
                    RuntimeInput(
                        touches=(
                            TouchEvent("up", target.right + 10, target.y + 1),
                        ),
                        fn_held=True,
                    )
                )

                self.assertEqual(down.intents, ())
                self.assertEqual(released.intents, (action,))
                self.assertEqual(duplicate.intents, ())

    def test_unlock_refreshes_verified_agent_state_before_exposing_agents(self) -> None:
        agents = ContactAgentWorkflow()
        lock = FakeLockSource(LockState.LOCKED)
        workflow = SystemLayerWorkflow(agents, lock)
        locked = workflow.render(RuntimeInput())
        self.assertIsInstance(locked.frame.workflow_frame, MediaLayerFrame)

        lock.current = LockState.UNLOCKED
        unlocked = workflow.render(RuntimeInput())

        self.assertEqual(agents.refresh_count, 1)
        self.assertEqual(
            unlocked.frame.workflow_frame,
            {"agent_identity": "fresh-agent"},
        )

    def test_unavailable_lock_state_is_the_same_privacy_safe_media_layer(self) -> None:
        unavailable_agents = ContactAgentWorkflow()
        unavailable = SystemLayerWorkflow(
            unavailable_agents,
            FakeLockSource(LockState.UNAVAILABLE),
        ).render(RuntimeInput())
        locked = SystemLayerWorkflow(
            ContactAgentWorkflow(),
            FakeLockSource(LockState.LOCKED),
        ).render(RuntimeInput())

        self.assertEqual(unavailable.frame.workflow_frame, locked.frame.workflow_frame)
        self.assertIsInstance(unavailable.frame.workflow_frame, MediaLayerFrame)
        self.assertEqual(unavailable_agents.events, [])
        self.assertNotIn("codex", repr(unavailable.frame.workflow_frame))

    def test_next_render_observes_lock_before_accepting_workflow_touch(self) -> None:
        outputs = iter(("false\n", "true\n"))
        calls = 0

        def run(command: list[str], **_kwargs):
            nonlocal calls
            calls += 1
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=next(outputs),
                stderr="",
            )

        agents = ContactAgentWorkflow()
        workflow = SystemLayerWorkflow(
            agents,
            LiveOmarchyLockSource(run=run),
        )
        workflow.render(RuntimeInput())

        locked = workflow.render(
            RuntimeInput(
                touches=(
                    TouchEvent("down", 40, 20),
                    TouchEvent("up", 40, 20),
                )
            )
        )

        self.assertEqual(calls, 2)
        self.assertIsInstance(locked.frame.workflow_frame, MediaLayerFrame)
        self.assertEqual(agents.events, [])

    def test_fn_release_cancels_an_active_media_contact_before_showing_agents(self) -> None:
        agents = ContactAgentWorkflow()
        workflow = SystemLayerWorkflow(agents, FakeLockSource())
        initial = workflow.render(RuntimeInput(fn_held=True))
        layer = initial.frame.workflow_frame
        assert isinstance(layer, MediaLayerFrame)
        target = layer.buttons[0].target
        pressed = workflow.render(
            RuntimeInput(
                touches=(TouchEvent("down", target.x + 1, target.y + 1),),
                fn_held=True,
            )
        )
        pressed_layer = pressed.frame.workflow_frame
        assert isinstance(pressed_layer, MediaLayerFrame)
        self.assertTrue(pressed_layer.buttons[0].pressed)

        released = workflow.render(
            RuntimeInput(
                touches=(TouchEvent("up", target.x + 1, target.y + 1),),
                fn_held=False,
            )
        )

        self.assertEqual(released.intents, ())
        self.assertEqual(agents.cancel_count, 1)
        self.assertEqual(released.frame.workflow_frame, {"agent_identity": "fresh-agent"})

    def test_fn_release_batch_cannot_reach_the_agent_layer(self) -> None:
        agents = ContactAgentWorkflow()
        workflow = SystemLayerWorkflow(agents, FakeLockSource())
        initial = workflow.render(RuntimeInput(fn_held=True))
        layer = initial.frame.workflow_frame
        assert isinstance(layer, MediaLayerFrame)
        target = layer.buttons[0].target

        released = workflow.render(
            RuntimeInput(
                touches=(
                    TouchEvent("down", target.x + 1, target.y + 1),
                    TouchEvent("up", target.x + 1, target.y + 1),
                ),
                fn_held=False,
            )
        )

        self.assertEqual(released.intents, ())
        self.assertEqual(agents.events, [])
        self.assertEqual(released.frame.workflow_frame, {"agent_identity": "fresh-agent"})


class OmarchyLockAdapterTests(unittest.TestCase):
    def test_unavailable_source_retries_with_capped_exponential_backoff(self) -> None:
        clock = ManualClock()
        calls = 0

        def run(_command: list[str], **_kwargs):
            nonlocal calls
            calls += 1
            raise TimeoutError

        source = LiveOmarchyLockSource(
            run=run,
            clock=clock,
            poll_interval=0.25,
            failure_backoff_max=2.0,
        )

        self.assertEqual(source.state(), LockState.UNAVAILABLE)
        self.assertEqual(source.state(), LockState.UNAVAILABLE)
        self.assertEqual(calls, 1)

        for delay, expected_calls in (
            (0.25, 2),
            (0.5, 3),
            (1.0, 4),
            (2.0, 5),
            (2.0, 6),
        ):
            clock.advance(delay - 0.001)
            self.assertEqual(source.state(), LockState.UNAVAILABLE)
            self.assertEqual(calls, expected_calls - 1)
            clock.advance(0.001)
            self.assertEqual(source.state(), LockState.UNAVAILABLE)
            self.assertEqual(calls, expected_calls)

    def test_healthy_lock_verdict_resets_failure_backoff(self) -> None:
        clock = ManualClock()
        outputs: list[object] = [
            TimeoutError(),
            TimeoutError(),
            "true\n",
            TimeoutError(),
            TimeoutError(),
        ]
        calls = 0

        def run(command: list[str], **_kwargs):
            nonlocal calls
            calls += 1
            output = outputs.pop(0)
            if isinstance(output, BaseException):
                raise output
            return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

        source = LiveOmarchyLockSource(run=run, clock=clock)

        self.assertEqual(source.state(), LockState.UNAVAILABLE)
        clock.advance(0.25)
        self.assertEqual(source.state(), LockState.UNAVAILABLE)
        clock.advance(0.5)
        self.assertEqual(source.state(), LockState.LOCKED)

        clock.advance(0.25)
        self.assertEqual(source.state(), LockState.UNAVAILABLE)
        clock.advance(0.249)
        self.assertEqual(source.state(), LockState.UNAVAILABLE)
        self.assertEqual(calls, 4)
        clock.advance(0.001)
        self.assertEqual(source.state(), LockState.UNAVAILABLE)
        self.assertEqual(calls, 5)

    def test_exact_is_locked_results_are_bounded_and_fail_closed(self) -> None:
        clock = ManualClock()
        calls: list[tuple[list[str], dict[str, object]]] = []
        outputs: list[object] = ["false\n", "true\n", "not-a-lock-state\n", TimeoutError()]

        def run(command: list[str], **kwargs):
            calls.append((command, kwargs))
            output = outputs.pop(0)
            if isinstance(output, BaseException):
                raise output
            return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

        source = LiveOmarchyLockSource(
            run=run,
            timeout=0.25,
            clock=clock,
            poll_interval=0.0,
        )

        self.assertEqual(source.state(), LockState.UNLOCKED)
        self.assertEqual(source.state(), LockState.LOCKED)
        self.assertEqual(source.state(), LockState.UNAVAILABLE)
        clock.advance(0.25)
        self.assertEqual(source.state(), LockState.UNAVAILABLE)
        self.assertEqual(
            [command for command, _kwargs in calls],
            [["omarchy-shell", "lock", "isLocked"]] * 4,
        )
        self.assertTrue(all(kwargs["timeout"] == 0.25 for _command, kwargs in calls))
        self.assertTrue(
            all(
                kwargs["env"]["OMARCHY_SHELL_IPC_TIMEOUT"] == "0.25s"
                for _command, kwargs in calls
            )
        )

    def test_privacy_safe_lock_states_are_rate_bounded(self) -> None:
        clock = ManualClock()
        outputs = iter(("true\n", "false\n"))
        calls = 0

        def run(command: list[str], **_kwargs):
            nonlocal calls
            calls += 1
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=next(outputs),
                stderr="",
            )

        source = LiveOmarchyLockSource(
            run=run,
            clock=clock,
            poll_interval=0.25,
        )

        self.assertEqual(source.state(), LockState.LOCKED)
        self.assertEqual(source.state(), LockState.LOCKED)
        clock.advance(0.249)
        self.assertEqual(source.state(), LockState.LOCKED)
        self.assertEqual(calls, 1)

        clock.advance(0.001)
        self.assertEqual(source.state(), LockState.UNLOCKED)
        self.assertEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()
