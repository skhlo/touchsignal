from __future__ import annotations

import unittest
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from touchbar_owner.herdr import HerdrSnapshot
from touchbar_owner.thermal import (
    GpuRuntimeState,
    LiveThermalAdapter,
    TemperatureReading,
    ThermalSnapshot,
)
from touchbar_owner.types import TouchEvent
from touchbar_owner.workflow import (
    CPU_TARGET_WIDTH,
    GPU_TARGET_WIDTH,
    HARDWARE_GAP,
    HARDWARE_RIGHT_PADDING,
    HerdrWorkflow,
    cpu_temperature_geometry,
    gpu_temperature_geometry,
)


@dataclass
class FakeHerdrSource:
    subscribers: list[Callable[[], None]] = field(default_factory=list)

    def snapshot(self) -> HerdrSnapshot:
        return HerdrSnapshot(available=True)

    def subscribe(self, on_change: Callable[[], None]) -> None:
        self.subscribers.append(on_change)


@dataclass
class FakeHerdrActions:
    agent_focuses: list[str] = field(default_factory=list)
    workspace_focuses: list[str] = field(default_factory=list)

    def request_agent_focus(self, pane_id: str) -> bool:
        self.agent_focuses.append(pane_id)
        return True

    def request_workspace_focus(self, workspace_id: str) -> bool:
        self.workspace_focuses.append(workspace_id)
        return True


@dataclass
class FakeThermalSource:
    current: ThermalSnapshot
    snapshots: int = 0

    def snapshot(self) -> ThermalSnapshot:
        self.snapshots += 1
        return self.current


@dataclass
class RecordingReader:
    values: dict[Path, str | Exception]
    calls: list[Path] = field(default_factory=list)

    def __call__(self, path: Path) -> str:
        self.calls.append(path)
        value = self.values[path]
        if isinstance(value, Exception):
            raise value
        return value


class ManualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def thermal_workflow(
    snapshot: ThermalSnapshot,
) -> tuple[HerdrWorkflow, FakeThermalSource, FakeHerdrActions]:
    source = FakeThermalSource(snapshot)
    actions = FakeHerdrActions()
    workflow = HerdrWorkflow(
        FakeHerdrSource(),
        actions,
        thermal_source=source,
    )
    return workflow, source, actions


class ThermalLayoutTests(unittest.TestCase):
    def test_right_cluster_places_cpu_and_gpu_at_the_right_edge(self) -> None:
        workflow, _, _ = thermal_workflow(ThermalSnapshot.unavailable())

        frame = workflow.frame()

        self.assertEqual(frame.cpu_temperature.target, cpu_temperature_geometry())
        self.assertEqual(frame.gpu_temperature.target, gpu_temperature_geometry())
        self.assertFalse(hasattr(frame, "power_slot"))
        self.assertEqual(frame.cpu_temperature.target.width, CPU_TARGET_WIDTH)
        self.assertEqual(frame.gpu_temperature.target.width, GPU_TARGET_WIDTH)
        self.assertEqual(
            frame.gpu_temperature.target.right + HARDWARE_RIGHT_PADDING + 1,
            2008,
        )
        self.assertEqual(
            frame.cpu_temperature.target.right + HARDWARE_GAP + 1,
            frame.gpu_temperature.target.x,
        )
        self.assertEqual(frame.cpu_temperature.target.x, 1698)
        self.assertEqual(frame.gpu_temperature.target.x, 1852)
        self.assertEqual(frame.reserved_center.right + 1, frame.cpu_temperature.target.x)

    def test_cpu_temperature_and_unavailable_presentations(self) -> None:
        available = ThermalSnapshot(
            cpu=TemperatureReading.measured(56),
            gpu_runtime=GpuRuntimeState.SUSPENDED,
            gpu=TemperatureReading.unavailable(),
        )
        workflow, source, _ = thermal_workflow(available)

        cpu = workflow.frame().cpu_temperature
        self.assertEqual(cpu.value, "56°C")
        self.assertTrue(cpu.available)
        self.assertFalse(cpu.dimmed)
        self.assertEqual(cpu.band, "normal")

        source.current = ThermalSnapshot.unavailable()
        cpu = workflow.frame().cpu_temperature
        self.assertEqual(cpu.value, "--°C")
        self.assertFalse(cpu.available)
        self.assertTrue(cpu.attention)

    def test_gpu_active_suspended_unknown_and_sensor_failure_presentations(self) -> None:
        cases = (
            (
                ThermalSnapshot(
                    cpu=TemperatureReading.measured(55),
                    gpu_runtime=GpuRuntimeState.ACTIVE,
                    gpu=TemperatureReading.measured(62),
                ),
                "62°C",
                False,
            ),
            (
                ThermalSnapshot(
                    cpu=TemperatureReading.measured(55),
                    gpu_runtime=GpuRuntimeState.SUSPENDED,
                    gpu=TemperatureReading.unavailable(),
                ),
                "--°C",
                True,
            ),
            (
                ThermalSnapshot(
                    cpu=TemperatureReading.measured(55),
                    gpu_runtime=GpuRuntimeState.UNKNOWN,
                    gpu=TemperatureReading.unavailable(),
                ),
                "--°C",
                True,
            ),
            (
                ThermalSnapshot(
                    cpu=TemperatureReading.measured(55),
                    gpu_runtime=GpuRuntimeState.UNAVAILABLE,
                    gpu=TemperatureReading.unavailable(),
                ),
                "--°C",
                True,
            ),
            (
                ThermalSnapshot(
                    cpu=TemperatureReading.measured(55),
                    gpu_runtime=GpuRuntimeState.ACTIVE,
                    gpu=TemperatureReading.unavailable(),
                ),
                "--°C",
                True,
            ),
        )

        for snapshot, value, dimmed in cases:
            with self.subTest(runtime=snapshot.gpu_runtime, sensor=snapshot.gpu.available):
                workflow, _, _ = thermal_workflow(snapshot)
                gpu = workflow.frame().gpu_temperature
                self.assertEqual(gpu.value, value)
                self.assertEqual(gpu.dimmed, dimmed)

    def test_temperature_tiles_have_no_touch_action(self) -> None:
        workflow, _, actions = thermal_workflow(ThermalSnapshot.unavailable())
        for target in (cpu_temperature_geometry(), gpu_temperature_geometry()):
            x = target.x + target.width // 2
            y = target.y + target.height // 2
            workflow.process_touch_events(
                [TouchEvent("down", x, y), TouchEvent("up", x, y)]
            )

        self.assertEqual(actions.agent_focuses, [])
        self.assertEqual(actions.workspace_focuses, [])


class ThermalSafetyEnvelopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cpu_path = Path("/sensors/cpu")
        self.runtime_path = Path("/gpu/power/runtime_status")
        self.gpu_path = Path("/gpu/hwmon/temp1_input")

    def adapter(self, reader: RecordingReader) -> LiveThermalAdapter:
        return LiveThermalAdapter(
            cpu_temperature_path=self.cpu_path,
            gpu_runtime_status_path=self.runtime_path,
            gpu_temperature_path=self.gpu_path,
            read_text=reader,
            snapshot_interval=0.0,
        )

    def test_active_runtime_reads_temperature_after_runtime_state(self) -> None:
        reader = RecordingReader(
            {
                self.cpu_path: "56000\n",
                self.runtime_path: "active\n",
                self.gpu_path: "62000\n",
            }
        )

        snapshot = self.adapter(reader).snapshot()

        self.assertEqual(snapshot.gpu_runtime, GpuRuntimeState.ACTIVE)
        self.assertEqual(snapshot.gpu.celsius, 62)
        self.assertEqual(
            reader.calls,
            [self.cpu_path, self.runtime_path, self.gpu_path],
        )

    def test_non_active_runtime_never_accesses_temperature_source(self) -> None:
        states = (
            ("suspended\n", GpuRuntimeState.SUSPENDED),
            ("suspending\n", GpuRuntimeState.UNKNOWN),
            (OSError("missing runtime state"), GpuRuntimeState.UNAVAILABLE),
        )
        for runtime_value, expected in states:
            with self.subTest(runtime=runtime_value):
                reader = RecordingReader(
                    {
                        self.cpu_path: "56000\n",
                        self.runtime_path: runtime_value,
                        self.gpu_path: AssertionError("temperature source accessed"),
                    }
                )

                snapshot = self.adapter(reader).snapshot()

                self.assertEqual(snapshot.gpu_runtime, expected)
                self.assertFalse(snapshot.gpu.available)
                self.assertEqual(reader.calls, [self.cpu_path, self.runtime_path])

    def test_suspended_runtime_cannot_bypass_gate_through_path_discovery(self) -> None:
        reader = RecordingReader(
            {
                self.cpu_path: "56000\n",
                self.runtime_path: "suspended\n",
            }
        )

        class DiscoveryProbe(LiveThermalAdapter):
            def _discover_gpu_temperature(self, runtime_path: Path) -> Path | None:
                raise AssertionError(
                    f"temperature discovery bypassed runtime gate: {runtime_path}"
                )

        adapter = DiscoveryProbe(
            cpu_temperature_path=self.cpu_path,
            gpu_runtime_status_path=self.runtime_path,
            read_text=reader,
            snapshot_interval=0.0,
        )

        snapshot = adapter.snapshot()

        self.assertEqual(snapshot.gpu_runtime, GpuRuntimeState.SUSPENDED)
        self.assertFalse(snapshot.gpu.available)
        self.assertEqual(reader.calls, [self.cpu_path, self.runtime_path])

    def test_sensor_parse_failures_are_explicitly_unavailable(self) -> None:
        reader = RecordingReader(
            {
                self.cpu_path: "not-a-temperature\n",
                self.runtime_path: "active\n",
                self.gpu_path: OSError("sensor missing"),
            }
        )

        snapshot = self.adapter(reader).snapshot()

        self.assertFalse(snapshot.cpu.available)
        self.assertEqual(snapshot.gpu_runtime, GpuRuntimeState.ACTIVE)
        self.assertFalse(snapshot.gpu.available)

    def test_small_temperature_jitter_does_not_republish_the_frame(self) -> None:
        clock = ManualClock()
        reader = RecordingReader(
            {
                self.cpu_path: "56000\n",
                self.runtime_path: "active\n",
                self.gpu_path: "62000\n",
            }
        )
        adapter = LiveThermalAdapter(
            cpu_temperature_path=self.cpu_path,
            gpu_runtime_status_path=self.runtime_path,
            gpu_temperature_path=self.gpu_path,
            read_text=reader,
            clock=clock,
            snapshot_interval=2.0,
            display_delta_c=3,
            max_display_interval=30.0,
        )

        first = adapter.snapshot()
        reader.values[self.cpu_path] = "57000\n"
        reader.values[self.gpu_path] = "63000\n"
        clock.advance(2.0)

        self.assertIs(adapter.snapshot(), first)

        reader.values[self.cpu_path] = "59000\n"
        reader.values[self.gpu_path] = "65000\n"
        clock.advance(2.0)
        changed = adapter.snapshot()

        self.assertEqual(changed.cpu.celsius, 59)
        self.assertEqual(changed.gpu.celsius, 65)

    def test_temperature_band_crossing_republishes_immediately(self) -> None:
        clock = ManualClock()
        reader = RecordingReader(
            {
                self.cpu_path: "79000\n",
                self.runtime_path: "active\n",
                self.gpu_path: "75000\n",
            }
        )
        adapter = LiveThermalAdapter(
            cpu_temperature_path=self.cpu_path,
            gpu_runtime_status_path=self.runtime_path,
            gpu_temperature_path=self.gpu_path,
            read_text=reader,
            clock=clock,
            snapshot_interval=2.0,
            display_delta_c=3,
        )
        adapter.snapshot()
        reader.values[self.cpu_path] = "80000\n"
        reader.values[self.gpu_path] = "76000\n"
        clock.advance(2.0)

        changed = adapter.snapshot()

        self.assertEqual(changed.cpu.celsius, 80)
        self.assertEqual(changed.gpu.celsius, 76)


if __name__ == "__main__":
    unittest.main()
