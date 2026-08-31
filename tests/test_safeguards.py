from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from touchbar_owner.chatgpt import HyprlandSnapshot
from touchbar_owner.cli import build_product_runtime
from touchbar_owner.drm import draw_runtime_frame
from touchbar_owner.herdr import HerdrSnapshot
from touchbar_owner.host import FakeHost
from touchbar_owner.runtime import SupervisedRuntime
from touchbar_owner.safeguards import PixelShiftRenderer
from touchbar_owner.system_layer import (
    MEDIA_ACTIONS,
    MediaButtonFrame,
    MediaLayerFrame,
    media_target_geometry,
    LockState,
)
from touchbar_owner.types import (
    MediaAction,
    RuntimeFrame,
    RuntimeInput,
    RuntimeResult,
)


class ManualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class StaticRenderer:
    def __init__(self) -> None:
        self.workflow_frame = {"target": (0, 7, 112, 46)}

    def render(self, _runtime_input: RuntimeInput) -> RuntimeResult:
        return RuntimeResult(
            frame=RuntimeFrame(
                surface_size=(2008, 60),
                touch_count=0,
                last_touch=None,
                workflow_frame=self.workflow_frame,
            ),
            intents=(MediaAction.NEXT,),
        )


class EmptyHyprlandSource:
    def snapshot(self) -> HyprlandSnapshot:
        return HyprlandSnapshot.unavailable()


class EmptyChatGPTActions:
    def request_focus(self, _client) -> None:
        return None

    def request_launch(self) -> None:
        return None


class EmptyHerdrSource:
    def snapshot(self) -> HerdrSnapshot:
        return HerdrSnapshot(available=True)

    def subscribe(self, _callback) -> None:
        return None


class EmptyHerdrActions:
    def request_agent_focus(self, _pane_id: str) -> bool:
        return True

    def request_workspace_focus(self, _workspace_id: str) -> bool:
        return True


class UnlockedSource:
    def state(self) -> LockState:
        return LockState.UNLOCKED


class RecordingContext:
    def __init__(self) -> None:
        self.operations: list[tuple[object, ...]] = []

    def rectangle(self, x: float, y: float, width: float, height: float) -> None:
        self.operations.append(("rectangle", x, y, width, height))

    def save(self) -> None:
        self.operations.append(("save",))

    def translate(self, x: float, y: float) -> None:
        self.operations.append(("translate", x, y))

    def restore(self) -> None:
        self.operations.append(("restore",))

    def __getattr__(self, _name: str):
        return lambda *_args, **_kwargs: None


class PixelShiftTests(unittest.TestCase):
    def test_nine_position_cadence_moves_pixels_without_moving_hit_geometry(self) -> None:
        clock = ManualClock()
        base = StaticRenderer()
        renderer = PixelShiftRenderer(base, clock=clock, cadence=60.0)
        expected = (
            (0, 0),
            (1, 0),
            (1, 1),
            (0, 1),
            (-1, 1),
            (-1, 0),
            (-1, -1),
            (0, -1),
            (1, -1),
            (0, 0),
        )

        initial = renderer.render(RuntimeInput())
        self.assertEqual(initial.frame.content_offset, (0, 0))

        clock.now = 59.999
        before_cadence = renderer.render(RuntimeInput())
        self.assertEqual(before_cadence.frame.content_offset, (0, 0))

        for index, offset in enumerate(expected[1:], start=1):
            clock.now = index * 60.0
            result = renderer.render(RuntimeInput())

            self.assertEqual(result.frame.content_offset, offset)
            self.assertIs(result.frame.workflow_frame, base.workflow_frame)
            self.assertEqual(result.intents, (MediaAction.NEXT,))

    def test_cadence_change_presents_a_new_frame_without_workflow_change(self) -> None:
        clock = ManualClock()
        renderer = PixelShiftRenderer(StaticRenderer(), clock=clock, cadence=60.0)
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw))
            runtime = SupervisedRuntime(host, renderer=renderer)
            runtime.start()
            try:
                self.assertEqual(len(host.presented_frames), 1)

                clock.now = 59.999
                runtime.process_once()
                self.assertEqual(len(host.presented_frames), 1)

                clock.now = 60.0
                runtime.process_once()
                self.assertEqual(len(host.presented_frames), 2)
            finally:
                runtime.stop()

        self.assertEqual(host.presented_frames[0].content_offset, (0, 0))
        self.assertEqual(host.presented_frames[1].content_offset, (1, 0))
        self.assertIs(
            host.presented_frames[0].workflow_frame,
            host.presented_frames[1].workflow_frame,
        )

    def test_draw_applies_offset_after_the_fixed_panel_background(self) -> None:
        layer = MediaLayerFrame(
            surface_size=(2008, 60),
            buttons=tuple(
                MediaButtonFrame(action, media_target_geometry(index))
                for index, action in enumerate(MEDIA_ACTIONS)
            ),
        )
        frame = RuntimeFrame(
            surface_size=(2008, 60),
            touch_count=0,
            last_touch=None,
            workflow_frame=layer,
            content_offset=(1, -1),
        )
        context = RecordingContext()

        draw_runtime_frame(context, 2008, 60, frame)

        background = ("rectangle", 0, 0, 2008, 60)
        translation = ("translate", 1, -1)
        self.assertIn(background, context.operations)
        self.assertIn(translation, context.operations)
        self.assertLess(
            context.operations.index(background),
            context.operations.index(translation),
        )
        self.assertEqual(context.operations.count(("save",)), 1)
        self.assertEqual(context.operations.count(("restore",)), 1)

    def test_product_runtime_uses_the_pixel_shift_cadence(self) -> None:
        clock = ManualClock()
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw))
            runtime = build_product_runtime(
                host,
                restart_delay=0.0,
                source=EmptyHyprlandSource(),
                actions=EmptyChatGPTActions(),
                herdr_source=EmptyHerdrSource(),
                herdr_actions=EmptyHerdrActions(),
                lock_source=UnlockedSource(),
                pixel_shift_clock=clock,
            )
            runtime.start()
            try:
                self.assertEqual(host.presented_frames[-1].content_offset, (0, 0))

                clock.now = 60.0
                runtime.process_once()
            finally:
                runtime.stop()

        self.assertEqual(host.presented_frames[-1].content_offset, (1, 0))


if __name__ == "__main__":
    unittest.main()
