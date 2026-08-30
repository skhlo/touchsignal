from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from touchbar_owner.host import FakeHost
from touchbar_owner.install import (
    SERVICE_NAME,
    install_user_service,
    remove_user_service,
    render_user_service,
    user_service_path,
)
from touchbar_owner.runtime import PreflightError, ProofRenderer, SupervisedRuntime
from touchbar_owner.types import (
    DRM_CONFIG,
    FIRMWARE_CONFIG,
    NATIVE_HEIGHT,
    NATIVE_WIDTH,
    OBSERVED_BASELINE,
    FnEvent,
    MediaAction,
    RuntimeFrame,
    RuntimeInput,
    RuntimeResult,
    TouchEvent,
)


class FailingRenderer(ProofRenderer):
    def __init__(self, fail_on: int) -> None:
        self.fail_on = fail_on
        self.calls = 0

    def render(self, runtime_input: RuntimeInput) -> RuntimeResult:
        self.calls += 1
        if self.calls == self.fail_on:
            raise RuntimeError("renderer failed")
        return super().render(runtime_input)


class IntentRenderer:
    def __init__(self) -> None:
        self.inputs: list[RuntimeInput] = []

    def render(self, runtime_input: RuntimeInput) -> RuntimeResult:
        self.inputs.append(runtime_input)
        frame = RuntimeFrame(
            surface_size=(NATIVE_WIDTH, NATIVE_HEIGHT),
            touch_count=len(runtime_input.touches),
            last_touch=(
                runtime_input.touches[-1] if runtime_input.touches else None
            ),
        )
        intents = (MediaAction.NEXT,) if runtime_input.touches else ()
        return RuntimeResult(frame=frame, intents=intents)


class RuntimePreflightTests(unittest.TestCase):
    def test_preflight_refuses_unsupported_hardware_without_partial_attach(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw), model="MacBookPro15,1")
            runtime = SupervisedRuntime(host)

            with self.assertRaisesRegex(PreflightError, "unsupported hardware"):
                runtime.start()

            self.assertEqual(host.firmware_row(), OBSERVED_BASELINE)
            self.assertEqual(host.operations, [])
            self.assertEqual(host.opened_drm_cards, [])

    def test_preflight_refuses_before_graphical_login(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw), graphical_session=False)
            runtime = SupervisedRuntime(host)

            with self.assertRaisesRegex(PreflightError, "graphical session"):
                runtime.start()

            self.assertEqual(host.firmware_row(), OBSERVED_BASELINE)
            self.assertEqual(host.operations, [])

    def test_preflight_refuses_missing_modules_without_attach(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw), missing_modules=("appletbdrm",))
            runtime = SupervisedRuntime(host)

            with self.assertRaisesRegex(PreflightError, "appletbdrm"):
                runtime.start()

            self.assertEqual(host.firmware_row(), OBSERVED_BASELINE)
            self.assertEqual(host.operations, [])

    def test_preflight_refuses_missing_permissions_without_attach(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw), permission_errors=("uinput write",))
            runtime = SupervisedRuntime(host)

            with self.assertRaisesRegex(PreflightError, "uinput"):
                runtime.start()

            self.assertEqual(host.firmware_row(), OBSERVED_BASELINE)
            self.assertEqual(host.operations, [])

    def test_start_restores_stale_custom_attachment_after_lock_before_reclaim(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(
                Path(raw),
                usb_configuration=DRM_CONFIG,
                appletbdrm_loaded=True,
            )
            runtime = SupervisedRuntime(host)
            runtime.start()
            try:
                self.assertEqual(host.usb_configuration, DRM_CONFIG)
                self.assertLess(host.operations.index("lock"), host.operations.index("restore"))
                self.assertLess(host.operations.index("restore"), host.operations.index("attach"))
                self.assertEqual(host.opened_drm_cards, ["/dev/dri/card3"])
            finally:
                runtime.stop()
            self.assertEqual(host.firmware_row(), OBSERVED_BASELINE)

    def test_competing_renderer_plus_stale_config_two_does_not_restore_or_attach(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(
                Path(raw),
                usb_configuration=DRM_CONFIG,
                appletbdrm_loaded=True,
                competing=("tiny-dfr",),
            )
            runtime = SupervisedRuntime(host)

            with self.assertRaisesRegex(PreflightError, "tiny-dfr"):
                runtime.start()

            self.assertEqual(host.operations, [])
            self.assertEqual(host.opened_drm_cards, [])
            self.assertEqual(host.usb_configuration, DRM_CONFIG)
            self.assertTrue(host.appletbdrm_loaded)


class RuntimeOperationTests(unittest.TestCase):
    def test_runtime_dispatches_renderer_intents_through_the_physical_owner(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw))
            host.queued_fn_events = [FnEvent("press")]
            host.queued_touch_events = [TouchEvent("up", 100, 20)]
            renderer = IntentRenderer()
            runtime = SupervisedRuntime(host, renderer=renderer)

            runtime.start()
            try:
                runtime.process_once()
            finally:
                runtime.stop()

        self.assertTrue(renderer.inputs[-1].fn_held)
        self.assertEqual(host.emitted_media_actions, [MediaAction.NEXT])
        self.assertEqual([action.kind for action in runtime.state.actions], ["next"])

    def test_unchanged_visual_frame_is_not_presented_again(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw))
            runtime = SupervisedRuntime(host)
            runtime.start()
            try:
                self.assertEqual(len(host.presented_frames), 1)

                runtime.process_once()
                runtime.process_once()

                self.assertEqual(len(host.presented_frames), 1)
            finally:
                runtime.stop()

    def test_runtime_carries_touch_through_normalized_input_and_frame(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw))
            host.queued_touch_events = [
                TouchEvent("down", 100, 20),
                TouchEvent("up", 100, 20),
            ]
            runtime = SupervisedRuntime(host)
            runtime.start()
            try:
                runtime.process_once()
                self.assertEqual([event.kind for event in runtime.state.touch_events], ["down", "up"])
                self.assertEqual(runtime.state.frames[-1].surface_size, (NATIVE_WIDTH, NATIVE_HEIGHT))
                self.assertEqual(runtime.state.frames[-1].touch_count, 2)
                self.assertEqual(runtime.state.frames[-1].last_touch, TouchEvent("up", 100, 20))
                self.assertEqual(runtime.state.actions, [])
            finally:
                runtime.stop()

    def test_logout_releases_devices_and_restores_the_firmware_row(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw))
            runtime = SupervisedRuntime(host)
            runtime.start()
            self.assertEqual(host.usb_configuration, DRM_CONFIG)

            host.graphical_session = False
            runtime.process_once()

            self.assertFalse(runtime.state.running)
            self.assertEqual(host.usb_configuration, FIRMWARE_CONFIG)
            self.assertEqual(host.firmware_row(), OBSERVED_BASELINE)
            self.assertEqual(
                set(host.closed_sessions),
                {
                    "display",
                    "touch",
                    "backlight",
                    "fn_input",
                    "virtual_keyboard",
                },
            )

    def test_renderer_failure_restores_before_supervised_restart_reacquires(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw))
            renderer = FailingRenderer(fail_on=2)
            runtime = SupervisedRuntime(
                host,
                renderer=renderer,
                wait=lambda _seconds: None,
                restart_delay=0.0,
            )

            runtime.run_supervised(cycles=1, max_restarts=1)

            self.assertEqual(runtime.state.restarts, 1)
            self.assertTrue(runtime.state.running)
            self.assertEqual(host.opened_drm_cards, ["/dev/dri/card3", "/dev/dri/card3"])
            restore_index = host.operations.index("restore")
            second_attach_index = host.operations.index("attach", restore_index + 1)
            self.assertLess(restore_index, second_attach_index)
            runtime.stop()
            self.assertEqual(host.firmware_row(), OBSERVED_BASELINE)


class RuntimeInstallTests(unittest.TestCase):
    def test_service_template_starts_after_graphical_session_and_restores_on_stop(self) -> None:
        service = render_user_service(Path("/usr/bin/touchsignal-touchbar-owner"))

        self.assertIn("After=graphical-session.target", service)
        self.assertIn("WantedBy=graphical-session.target", service)
        self.assertIn("ExecCondition=/usr/bin/touchsignal-touchbar-owner preflight", service)
        self.assertIn("ExecStart=/usr/bin/touchsignal-touchbar-owner run", service)
        self.assertIn("ExecStopPost=/usr/bin/touchsignal-touchbar-owner restore", service)
        self.assertIn("Restart=on-failure", service)

    def test_install_and_remove_touch_only_the_user_unit_and_restore(self) -> None:
        with TemporaryDirectory() as raw:
            home = Path(raw)
            calls: list[list[str]] = []

            def fake_run(command: list[str], check: bool = True) -> None:
                calls.append(command)

            target = install_user_service(
                home,
                Path("/usr/bin/touchsignal-touchbar-owner"),
                run=fake_run,
            )
            self.assertEqual(target, user_service_path(home))
            self.assertTrue(target.exists())
            self.assertIn(SERVICE_NAME, target.name)

            removed = remove_user_service(
                home,
                Path("/usr/bin/touchsignal-touchbar-owner"),
                run=fake_run,
            )
            self.assertEqual(removed, target)
            self.assertFalse(target.exists())
            self.assertIn(["systemctl", "--user", "enable", SERVICE_NAME], calls)
            self.assertIn(["systemctl", "--user", "disable", "--now", SERVICE_NAME], calls)
            self.assertIn(["/usr/bin/touchsignal-touchbar-owner", "restore"], calls)

    def test_remove_attempts_restore_when_daemon_reload_fails(self) -> None:
        with TemporaryDirectory() as raw:
            home = Path(raw)
            target = user_service_path(home)
            target.parent.mkdir(parents=True)
            target.write_text("stale service\n", encoding="utf-8")
            calls: list[list[str]] = []

            def fake_run(command: list[str], check: bool = True) -> None:
                calls.append(command)
                if command == ["systemctl", "--user", "daemon-reload"]:
                    raise subprocess.CalledProcessError(1, command)

            with self.assertRaises(subprocess.CalledProcessError):
                remove_user_service(
                    home,
                    Path("/usr/bin/touchsignal-touchbar-owner"),
                    run=fake_run,
                )

            self.assertIn(["systemctl", "--user", "daemon-reload"], calls)
            self.assertIn(["/usr/bin/touchsignal-touchbar-owner", "restore"], calls)
