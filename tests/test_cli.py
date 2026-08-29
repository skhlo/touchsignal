from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from touchbar_owner.cli import main
from touchbar_owner.host import FakeHost
from touchbar_owner.owner import TouchBarOwner
from touchbar_owner.types import OBSERVED_BASELINE, TouchEvent


class CliTests(unittest.TestCase):
    def test_status_prints_the_firmware_row(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            usb = root / "sys/bus/usb/devices/7-6"
            usb.mkdir(parents=True)
            (usb / "idVendor").write_text("05ac\n")
            (usb / "idProduct").write_text("8302\n")
            (usb / "bConfigurationValue").write_text("1\n")
            kbd = root / "sys/module/hid_appletb_kbd/parameters"
            kbd.mkdir(parents=True)
            (kbd / "mode").write_text("2\n")
            (kbd / "fntoggle").write_text("Y\n")
            (kbd / "autodim").write_text("Y\n")
            bl = root / "sys/class/backlight/appletb_backlight"
            bl.mkdir(parents=True)
            (bl / "brightness").write_text("2\n")
            (bl / "actual_brightness").write_text("2\n")
            (root / "proc").mkdir()
            (root / "proc" / "modules").write_text("")
            (root / "sys/class/drm").mkdir(parents=True)
            with patch("touchbar_owner.cli.read_firmware_row") as read_row:
                read_row.return_value = OBSERVED_BASELINE
                with patch("touchbar_owner.cli.competing_renderer_names", return_value=()):
                    with patch("touchbar_owner.cli.list_drm_cards", return_value=[]):
                        with redirect_stdout(io.StringIO()):
                            self.assertEqual(main(["status"]), 0)

    def test_claim_restores_after_normal_stop(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw))
            host.queued_touch_events = [
                TouchEvent("down", 10, 10),
                TouchEvent("move", 20, 10),
                TouchEvent("up", 20, 10),
            ]
            created: list[TouchBarOwner] = []

            def factory(_live_host: object) -> TouchBarOwner:
                owner = TouchBarOwner(host)
                created.append(owner)
                return owner

            with patch("touchbar_owner.cli.LiveHost", return_value=host):
                with patch("touchbar_owner.cli.TouchBarOwner", side_effect=factory):
                    with redirect_stdout(io.StringIO()):
                        rc = main(["claim", "--seconds", "0"])
            self.assertEqual(rc, 0)
            self.assertEqual(host.firmware_row(), OBSERVED_BASELINE)
            self.assertTrue(created)

    def test_run_uses_the_product_runtime_factory(self) -> None:
        with TemporaryDirectory() as raw:
            host = FakeHost(Path(raw))
            calls: list[object] = []

            class OneCycleRuntime:
                def __init__(self) -> None:
                    self.state = SimpleNamespace(running=False)

                def run_supervised(self, cycles: int | None = None, max_restarts: int = 1) -> None:
                    calls.append(("run", cycles, max_restarts))
                    host.graphical_session = False

                def stop(self) -> None:
                    calls.append(("stop",))

            runtime = OneCycleRuntime()

            def factory(live_host: object, *, restart_delay: float) -> OneCycleRuntime:
                calls.append(("factory", live_host, restart_delay))
                return runtime

            with patch("touchbar_owner.cli.LiveHost", return_value=host):
                with patch("touchbar_owner.cli.build_product_runtime", side_effect=factory):
                    with redirect_stdout(io.StringIO()):
                        rc = main([
                            "run",
                            "--poll-interval",
                            "0",
                            "--restart-delay",
                            "0.25",
                            "--max-restarts",
                            "0",
                        ])

            self.assertEqual(rc, 0)
            self.assertEqual(calls[0], ("factory", host, 0.25))
            self.assertIn(("run", 1, 0), calls)
            self.assertIn(("stop",), calls)
