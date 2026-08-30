from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from time import monotonic
from typing import Protocol


DEFAULT_HWMON_ROOT = Path("/sys/class/hwmon")
DEFAULT_DRM_ROOT = Path("/sys/class/drm")
AMD_VENDOR_ID = "0x1002"
CPU_PACKAGE_LABEL = "Package id 0"
GPU_EDGE_LABEL = "edge"
MIN_TEMPERATURE_MILLIDEGREES = -50_000
MAX_TEMPERATURE_MILLIDEGREES = 150_000

ReadText = Callable[[Path], str]


class Clock(Protocol):
    def __call__(self) -> float: ...


class ThermalSource(Protocol):
    def snapshot(self) -> ThermalSnapshot: ...


class GpuRuntimeState(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class TemperatureReading:
    celsius: int | None
    available: bool

    @classmethod
    def measured(cls, celsius: int) -> TemperatureReading:
        return cls(celsius=celsius, available=True)

    @classmethod
    def unavailable(cls) -> TemperatureReading:
        return cls(celsius=None, available=False)


@dataclass(frozen=True)
class ThermalSnapshot:
    cpu: TemperatureReading
    gpu_runtime: GpuRuntimeState
    gpu: TemperatureReading

    @classmethod
    def unavailable(cls) -> ThermalSnapshot:
        return cls(
            cpu=TemperatureReading.unavailable(),
            gpu_runtime=GpuRuntimeState.UNAVAILABLE,
            gpu=TemperatureReading.unavailable(),
        )


def _read_path(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class LiveThermalAdapter:
    """Read CPU and dGPU temperatures without waking a suspended dGPU.

    The dGPU temperature source has one enforced home: ``snapshot`` reads the
    runtime-status oracle first and reaches the temperature path only from the
    exact ``active`` branch.
    """

    def __init__(
        self,
        *,
        cpu_temperature_path: Path | None = None,
        gpu_runtime_status_path: Path | None = None,
        gpu_temperature_path: Path | None = None,
        hwmon_root: Path = DEFAULT_HWMON_ROOT,
        drm_root: Path = DEFAULT_DRM_ROOT,
        read_text: ReadText = _read_path,
        clock: Clock = monotonic,
        snapshot_interval: float = 2.0,
    ) -> None:
        self.cpu_temperature_path = cpu_temperature_path
        self.gpu_runtime_status_path = gpu_runtime_status_path
        self.gpu_temperature_path = gpu_temperature_path
        self._gpu_temperature_path_is_explicit = gpu_temperature_path is not None
        self.hwmon_root = hwmon_root
        self.drm_root = drm_root
        self.read_text = read_text
        self.clock = clock
        self.snapshot_interval = snapshot_interval
        self._cached_snapshot = ThermalSnapshot.unavailable()
        self._snapshot_at: float | None = None

    def snapshot(self) -> ThermalSnapshot:
        now = self.clock()
        if (
            self._snapshot_at is not None
            and now >= self._snapshot_at
            and now - self._snapshot_at < self.snapshot_interval
        ):
            return self._cached_snapshot

        snapshot = self._capture_snapshot()
        self._cached_snapshot = snapshot
        self._snapshot_at = self.clock()
        return snapshot

    def _capture_snapshot(self) -> ThermalSnapshot:
        cpu_path = self.cpu_temperature_path or self._discover_cpu_temperature()
        if self.cpu_temperature_path is None and cpu_path is not None:
            self.cpu_temperature_path = cpu_path
        cpu = self._temperature(cpu_path)

        runtime_path = (
            self.gpu_runtime_status_path or self._discover_gpu_runtime_status()
        )
        if self.gpu_runtime_status_path is None and runtime_path is not None:
            self.gpu_runtime_status_path = runtime_path
        runtime = self._gpu_runtime(runtime_path)

        # Trust-envelope choke point: no other branch may resolve or access the
        # dGPU temperature source.
        if runtime != GpuRuntimeState.ACTIVE:
            return ThermalSnapshot(
                cpu=cpu,
                gpu_runtime=runtime,
                gpu=TemperatureReading.unavailable(),
            )

        gpu_path = self.gpu_temperature_path
        if gpu_path is None and runtime_path is not None:
            gpu_path = self._discover_gpu_temperature(runtime_path)
            if gpu_path is not None:
                self.gpu_temperature_path = gpu_path
        gpu = self._temperature(gpu_path)
        if (
            not gpu.available
            and self.gpu_temperature_path is not None
            and not self._gpu_temperature_path_is_explicit
        ):
            self.gpu_temperature_path = None
        return ThermalSnapshot(cpu=cpu, gpu_runtime=runtime, gpu=gpu)

    def _gpu_runtime(self, path: Path | None) -> GpuRuntimeState:
        raw = self._text(path)
        if raw is None:
            return GpuRuntimeState.UNAVAILABLE
        state = raw.strip().casefold()
        if state == "active":
            return GpuRuntimeState.ACTIVE
        if state == "suspended":
            return GpuRuntimeState.SUSPENDED
        return GpuRuntimeState.UNKNOWN

    def _temperature(self, path: Path | None) -> TemperatureReading:
        raw = self._text(path)
        if raw is None:
            return TemperatureReading.unavailable()
        try:
            millidegrees = int(raw.strip())
        except ValueError:
            return TemperatureReading.unavailable()
        if not (
            MIN_TEMPERATURE_MILLIDEGREES
            <= millidegrees
            <= MAX_TEMPERATURE_MILLIDEGREES
        ):
            return TemperatureReading.unavailable()
        return TemperatureReading.measured(round(millidegrees / 1000))

    def _text(self, path: Path | None) -> str | None:
        if path is None:
            return None
        try:
            return self.read_text(path)
        except (OSError, UnicodeError):
            return None

    def _discover_cpu_temperature(self) -> Path | None:
        for hwmon in sorted(self.hwmon_root.glob("hwmon*")):
            name = self._text(hwmon / "name")
            if name is None or name.strip() != "coretemp":
                continue
            for label_path in sorted(hwmon.glob("temp*_label")):
                label = self._text(label_path)
                if label is not None and label.strip() == CPU_PACKAGE_LABEL:
                    return label_path.with_name(
                        label_path.name.replace("_label", "_input")
                    )
        return None

    def _discover_gpu_runtime_status(self) -> Path | None:
        for card in sorted(self.drm_root.glob("card*")):
            if re.fullmatch(r"card\d+", card.name) is None:
                continue
            vendor = self._text(card / "device" / "vendor")
            if vendor is not None and vendor.strip().casefold() == AMD_VENDOR_ID:
                return card / "device" / "power" / "runtime_status"
        return None

    def _discover_gpu_temperature(self, runtime_path: Path) -> Path | None:
        device_path = runtime_path.parent.parent
        for hwmon in sorted((device_path / "hwmon").glob("hwmon*")):
            name = self._text(hwmon / "name")
            if name is None or name.strip() != "amdgpu":
                continue
            for label_path in sorted(hwmon.glob("temp*_label")):
                label = self._text(label_path)
                if label is not None and label.strip().casefold() == GPU_EDGE_LABEL:
                    return label_path.with_name(
                        label_path.name.replace("_label", "_input")
                    )
        return None
