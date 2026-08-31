from __future__ import annotations

from dataclasses import dataclass

from .types import NATIVE_HEIGHT, NATIVE_WIDTH

DIAGNOSTIC_STRIPE_WIDTH = 251
RED = (0.82, 0.28, 0.28)
WHITE = (1.0, 1.0, 1.0)


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int
    color: tuple[float, float, float]

    @property
    def right(self) -> int:
        return self.x + self.width - 1

    @property
    def bottom(self) -> int:
        return self.y + self.height - 1

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x <= self.right and self.y <= y <= self.bottom


@dataclass(frozen=True)
class TestSurfacePlan:
    canvas_width: int
    canvas_height: int
    stripes: tuple[Rect, ...]

    @property
    def first_stripe(self) -> Rect:
        return self.stripes[0]

    @property
    def last_stripe(self) -> Rect:
        return self.stripes[-1]


def test_surface_plan(width: int = NATIVE_WIDTH, height: int = NATIVE_HEIGHT) -> TestSurfacePlan:
    if (width, height) != (NATIVE_WIDTH, NATIVE_HEIGHT):
        raise ValueError(f"canvas is {width}x{height}, expected {NATIVE_WIDTH}x{NATIVE_HEIGHT}")
    stripes = []
    x = 0
    index = 0
    while x < width:
        stripe_width = min(DIAGNOSTIC_STRIPE_WIDTH, width - x)
        color = RED if index % 2 == 0 else WHITE
        stripes.append(Rect(x, 0, stripe_width, height, color))
        x += stripe_width
        index += 1
    plan = TestSurfacePlan(canvas_width=width, canvas_height=height, stripes=tuple(stripes))
    if plan.first_stripe.x != 0 or plan.last_stripe.right != width - 1:
        raise ValueError("diagnostic stripes do not cover the canvas")
    if plan.first_stripe.y != 0 or plan.first_stripe.bottom != height - 1:
        raise ValueError("diagnostic stripes do not cover the canvas height")
    return plan


def map_logical_to_physical(
    x: int,
    y: int,
    *,
    rotate90: bool,
    physical_width: int,
    physical_height: int,
) -> tuple[int, int]:
    if rotate90:
        dest_x = physical_width - 1 - y
        dest_y = x
        return dest_x, dest_y
    return x, y


def _u8_view(buf) -> memoryview:
    view = buf if isinstance(buf, memoryview) else memoryview(buf)
    if view.format != "B" or view.ndim != 1:
        view = view.cast("B")
    return view


def copy_logical_to_physical_scanout(
    src,
    *,
    src_stride: int,
    width: int,
    height: int,
    dest,
    dest_pitch: int,
    rotate90: bool,
) -> None:
    src_u8 = _u8_view(src)
    dest_u8 = _u8_view(dest)
    if rotate90:
        needed = (width - 1) * dest_pitch + height * 4
        if needed > dest_u8.nbytes:
            raise ValueError(f"scanout dest is {dest_u8.nbytes} bytes, need {needed} for pitch {dest_pitch}")
        for y in range(height):
            src_start = y * src_stride
            dest_start = (height - 1 - y) * 4
            dest_stop = dest_start + width * dest_pitch
            for channel in range(4):
                dest_u8[
                    dest_start + channel : dest_stop + channel : dest_pitch
                ] = src_u8[
                    src_start + channel : src_start + width * 4 + channel : 4
                ]
        return
    limit = height * src_stride
    if limit > dest_u8.nbytes:
        raise ValueError("scanout dest is too small")
    dest_u8[:limit] = src_u8[:limit]
