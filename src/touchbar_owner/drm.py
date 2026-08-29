from __future__ import annotations

import ctypes
import ctypes.util
import fcntl
import mmap
import os
from dataclasses import dataclass
from pathlib import Path

from .surface import copy_logical_to_physical_scanout, test_surface_plan
from .types import DrmCard, NATIVE_HEIGHT, NATIVE_WIDTH, RuntimeFrame

DRM_IOCTL_BASE = ord("d")


def _ioc(dir_bits: int, nr: int, size: int) -> int:
    return (dir_bits << 30) | (size << 16) | (DRM_IOCTL_BASE << 8) | nr


def _iowr(nr: int, size: int) -> int:
    return _ioc(3, nr, size)


class drm_mode_create_dumb(ctypes.Structure):
    _fields_ = [
        ("height", ctypes.c_uint32),
        ("width", ctypes.c_uint32),
        ("bpp", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("handle", ctypes.c_uint32),
        ("pitch", ctypes.c_uint32),
        ("size", ctypes.c_uint64),
    ]


class drm_mode_map_dumb(ctypes.Structure):
    _fields_ = [
        ("handle", ctypes.c_uint32),
        ("pad", ctypes.c_uint32),
        ("offset", ctypes.c_uint64),
    ]


class drm_mode_destroy_dumb(ctypes.Structure):
    _fields_ = [
        ("handle", ctypes.c_uint32),
    ]


class drmModeModeInfo(ctypes.Structure):
    _fields_ = [
        ("clock", ctypes.c_uint32),
        ("hdisplay", ctypes.c_uint16),
        ("hsync_start", ctypes.c_uint16),
        ("hsync_end", ctypes.c_uint16),
        ("htotal", ctypes.c_uint16),
        ("hskew", ctypes.c_uint16),
        ("vdisplay", ctypes.c_uint16),
        ("vsync_start", ctypes.c_uint16),
        ("vsync_end", ctypes.c_uint16),
        ("vtotal", ctypes.c_uint16),
        ("vscan", ctypes.c_uint16),
        ("vrefresh", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("name", ctypes.c_char * 32),
    ]


class drmModeRes(ctypes.Structure):
    _fields_ = [
        ("count_fbs", ctypes.c_int),
        ("fbs", ctypes.POINTER(ctypes.c_uint32)),
        ("count_crtcs", ctypes.c_int),
        ("crtcs", ctypes.POINTER(ctypes.c_uint32)),
        ("count_connectors", ctypes.c_int),
        ("connectors", ctypes.POINTER(ctypes.c_uint32)),
        ("count_encoders", ctypes.c_int),
        ("encoders", ctypes.POINTER(ctypes.c_uint32)),
        ("min_width", ctypes.c_uint32),
        ("max_width", ctypes.c_uint32),
        ("min_height", ctypes.c_uint32),
        ("max_height", ctypes.c_uint32),
    ]


class drmModeConnector(ctypes.Structure):
    _fields_ = [
        ("connector_id", ctypes.c_uint32),
        ("encoder_id", ctypes.c_uint32),
        ("connector_type", ctypes.c_uint32),
        ("connector_type_id", ctypes.c_uint32),
        ("connection", ctypes.c_uint32),
        ("mmWidth", ctypes.c_uint32),
        ("mmHeight", ctypes.c_uint32),
        ("subpixel", ctypes.c_uint32),
        ("count_modes", ctypes.c_int),
        ("modes", ctypes.POINTER(drmModeModeInfo)),
        ("count_props", ctypes.c_int),
        ("props", ctypes.POINTER(ctypes.c_uint32)),
        ("prop_values", ctypes.POINTER(ctypes.c_uint64)),
        ("count_encoders", ctypes.c_int),
        ("encoders", ctypes.POINTER(ctypes.c_uint32)),
    ]


class drmModeEncoder(ctypes.Structure):
    _fields_ = [
        ("encoder_id", ctypes.c_uint32),
        ("encoder_type", ctypes.c_uint32),
        ("crtc_id", ctypes.c_uint32),
        ("possible_crtcs", ctypes.c_uint32),
        ("possible_clones", ctypes.c_uint32),
    ]


class drmModeProperty(ctypes.Structure):
    _fields_ = [
        ("prop_id", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("name", ctypes.c_char * 32),
        ("count_values", ctypes.c_int),
        ("values", ctypes.POINTER(ctypes.c_uint64)),
        ("count_enums", ctypes.c_int),
        ("enums", ctypes.c_void_p),
        ("count_blobs", ctypes.c_int),
        ("blob_ids", ctypes.POINTER(ctypes.c_uint32)),
    ]


class drmModeClip(ctypes.Structure):
    _fields_ = [
        ("x1", ctypes.c_ushort),
        ("y1", ctypes.c_ushort),
        ("x2", ctypes.c_ushort),
        ("y2", ctypes.c_ushort),
    ]


DRM_MODE_CREATE_DUMB = _iowr(0xB2, ctypes.sizeof(drm_mode_create_dumb))
DRM_MODE_MAP_DUMB = _iowr(0xB3, ctypes.sizeof(drm_mode_map_dumb))
DRM_MODE_DESTROY_DUMB = _iowr(0xB4, ctypes.sizeof(drm_mode_destroy_dumb))
DRM_MODE_CONNECTED = 1

HERDR_STATE_COLORS = {
    "ready": (0.62, 0.68, 0.75),
    "working": (0.45, 0.75, 1.00),
    "blocked": (1.00, 0.68, 0.15),
    "done": (0.45, 0.90, 0.55),
    "unknown": (0.72, 0.72, 0.78),
    "unavailable": (1.00, 0.35, 0.40),
}

PANEL_BACKGROUND = (0.0, 0.0, 0.0)
TILE_BACKGROUND = (0.055, 0.060, 0.070)
TILE_PRESSED_BACKGROUND = (0.13, 0.14, 0.16)
TILE_PENDING_BACKGROUND = (0.11, 0.095, 0.045)
TILE_UNAVAILABLE_BACKGROUND = (0.035, 0.035, 0.040)


def select_connected_connector(probed, current):
    selected = None
    for option in (probed, current):
        if not option:
            continue
        if option.contents.connection == DRM_MODE_CONNECTED and option.contents.count_modes > 0:
            selected = option
            break
    unused = [option for option in (probed, current) if option and option != selected]
    return selected, unused


def draw_test_surface(ctx, width: int, height: int) -> None:
    plan = test_surface_plan(width, height)
    for stripe in plan.stripes:
        ctx.set_source_rgb(*stripe.color)
        ctx.rectangle(stripe.x, stripe.y, stripe.width, stripe.height)
        ctx.fill()


def draw_runtime_frame(ctx, width: int, height: int, frame: RuntimeFrame) -> None:
    if frame.surface_size != (width, height):
        raise RuntimeError(f"frame is {frame.surface_size}, expected {(width, height)}")
    workflow = frame.workflow_frame
    if workflow is None:
        draw_test_surface(ctx, width, height)
        return

    ctx.set_source_rgb(*PANEL_BACKGROUND)
    ctx.rectangle(0, 0, width, height)
    ctx.fill()

    tile = workflow.chatgpt_tile
    target = tile.target
    if not tile.capability_available or tile.touch_cancelled:
        background = TILE_UNAVAILABLE_BACKGROUND
    elif tile.pressed:
        background = TILE_PRESSED_BACKGROUND
    elif tile.pending:
        background = TILE_PENDING_BACKGROUND
    else:
        background = TILE_BACKGROUND
    ctx.set_source_rgb(*background)
    ctx.rectangle(target.x, target.y, target.width, target.height)
    ctx.fill()

    _draw_logo_box(ctx, tile.logo_box, tile.logo_asset)
    _draw_status_sign(ctx, tile.status_box, str(tile.status_sign))

    for herdr_tile in workflow.herdr_tiles:
        _draw_herdr_tile(ctx, herdr_tile)

    reserved = workflow.reserved_center
    if reserved is not None:
        ctx.set_source_rgb(*PANEL_BACKGROUND)
        ctx.rectangle(reserved.x, reserved.y, reserved.width, reserved.height)
        ctx.fill()


def _draw_logo_box(ctx, box, asset: str | None) -> None:
    _draw_identity_box(ctx, box, "C", asset)


def _draw_identity_box(ctx, box, label: str, asset: str | None = None) -> None:
    ctx.set_source_rgb(0.11, 0.13, 0.16)
    ctx.rectangle(box.x, box.y, box.width, box.height)
    ctx.fill()
    if asset is not None and _draw_logo_asset(ctx, box, asset):
        return
    ctx.set_source_rgb(0.72, 0.84, 0.94)
    ctx.select_font_face("Sans")
    ctx.set_font_size(16 if len(label) > 1 else 21)
    _show_centered_text(ctx, label, box.x, box.y, box.width, box.height)


def _draw_herdr_tile(ctx, tile) -> None:
    target = tile.target
    if tile.touch_cancelled or tile.source_lost or tile.focus_failed:
        background = TILE_UNAVAILABLE_BACKGROUND
    elif tile.pressed:
        background = TILE_PRESSED_BACKGROUND
    elif tile.pending:
        background = TILE_PENDING_BACKGROUND
    else:
        background = TILE_BACKGROUND
    ctx.set_source_rgb(*background)
    ctx.rectangle(target.x, target.y, target.width, target.height)
    ctx.fill()

    ctx.set_source_rgb(0.82, 0.85, 0.90)
    ctx.select_font_face("Sans")
    ctx.set_font_size(13)
    _show_centered_text(
        ctx,
        str(tile.workspace_number),
        target.x + 2,
        target.y,
        19,
        target.height,
    )

    identity = tile.identity_fallback or _workspace_fallback(tile.workspace_label)
    if tile.logo_box is not None:
        _draw_identity_box(ctx, tile.logo_box, identity, tile.logo_asset)
    if tile.status_box is not None:
        _draw_status_sign(
            ctx,
            tile.status_box,
            str(tile.status_sign),
            color=HERDR_STATE_COLORS.get(tile.state_token),
        )


def _workspace_fallback(label: str | None) -> str:
    if not label:
        return "--"
    words = label.split()
    if len(words) > 1:
        return "".join(word[0] for word in words[:2]).upper()
    return label[:2].upper()


def _draw_logo_asset(ctx, box, asset: str) -> bool:
    path = _resolve_asset_path(asset)
    if path is None:
        return False
    try:
        if path.suffix.casefold() == ".png":
            import cairo

            surface = cairo.ImageSurface.create_from_png(str(path))
            padding = 1
            scale = min(
                (box.width - padding * 2) / surface.get_width(),
                (box.height - padding * 2) / surface.get_height(),
            )
            x = box.x + (box.width - surface.get_width() * scale) / 2
            y = box.y + (box.height - surface.get_height() * scale) / 2
            ctx.save()
            ctx.translate(x, y)
            ctx.scale(scale, scale)
            ctx.set_source_surface(surface, 0, 0)
            ctx.paint()
            ctx.restore()
            return True

        import gi

        gi.require_version("Rsvg", "2.0")
        from gi.repository import Rsvg

        handle = Rsvg.Handle.new_from_file(str(path))
        handle.set_stylesheet(b"svg { color: #8aadc9; }")
        viewport = Rsvg.Rectangle()
        viewport.x = box.x + 1
        viewport.y = box.y + 1
        viewport.width = box.width - 2
        viewport.height = box.height - 2
        handle.render_document(ctx, viewport)
        return True
    except Exception:
        return False


def _resolve_asset_path(asset: str) -> Path | None:
    relative = Path(asset)
    for candidate in (relative, Path(__file__).resolve().parents[2] / relative):
        if candidate.is_file():
            return candidate
    return None


def _draw_status_sign(ctx, box, sign: str, *, color=None) -> None:
    selected_color = color or {
        "dot": (0.41, 0.46, 0.54),
        "arrow": (0.54, 0.68, 0.80),
        "check": (0.64, 0.75, 0.55),
        "pending": (0.92, 0.80, 0.55),
        "bang": (0.75, 0.38, 0.42),
    }.get(sign, (0.41, 0.46, 0.54))
    ctx.set_source_rgb(*selected_color)
    cx = box.x + box.width / 2
    cy = box.y + box.height / 2
    radius = min(box.width, box.height) * 0.36
    if sign == "arrow":
        ctx.move_to(cx - radius, cy - radius)
        ctx.line_to(cx + radius, cy)
        ctx.line_to(cx - radius, cy + radius)
        ctx.close_path()
        ctx.fill()
    elif sign == "check":
        ctx.set_line_width(3)
        ctx.move_to(cx - radius, cy)
        ctx.line_to(cx - radius * 0.25, cy + radius * 0.65)
        ctx.line_to(cx + radius, cy - radius * 0.75)
        ctx.stroke()
    elif sign == "pending":
        ctx.set_line_width(3)
        ctx.arc(cx, cy, radius, 0.0, 5.0)
        ctx.stroke()
    elif sign == "bang":
        ctx.set_line_width(3)
        ctx.move_to(cx, cy - radius)
        ctx.line_to(cx, cy + radius * 0.35)
        ctx.stroke()
        ctx.arc(cx, cy + radius * 0.85, 1.5, 0.0, 6.283)
        ctx.fill()
    elif sign == "question":
        ctx.select_font_face("Sans")
        ctx.set_font_size(23)
        _show_centered_text(ctx, "?", box.x, box.y, box.width, box.height)
    else:
        ctx.arc(cx, cy, radius, 0.0, 6.283)
        ctx.fill()


def _show_centered_text(ctx, text: str, x: int, y: int, width: int, height: int) -> None:
    extents = ctx.text_extents(text)
    ctx.move_to(
        x + (width - extents.width) / 2 - extents.x_bearing,
        y + (height - extents.height) / 2 - extents.y_bearing,
    )
    ctx.show_text(text)


def copy_drm_mode(mode: drmModeModeInfo) -> drmModeModeInfo:
    copied = drmModeModeInfo()
    ctypes.memmove(ctypes.byref(copied), ctypes.byref(mode), ctypes.sizeof(drmModeModeInfo))
    return copied


def _libdrm() -> ctypes.CDLL:
    path = ctypes.util.find_library("drm") or "libdrm.so.2"
    lib = ctypes.CDLL(path)
    lib.drmModeGetResources.argtypes = [ctypes.c_int]
    lib.drmModeGetResources.restype = ctypes.POINTER(drmModeRes)
    lib.drmModeFreeResources.argtypes = [ctypes.POINTER(drmModeRes)]
    lib.drmModeFreeResources.restype = None
    lib.drmModeGetConnectorCurrent.argtypes = [ctypes.c_int, ctypes.c_uint32]
    lib.drmModeGetConnectorCurrent.restype = ctypes.POINTER(drmModeConnector)
    lib.drmModeGetConnector.argtypes = [ctypes.c_int, ctypes.c_uint32]
    lib.drmModeGetConnector.restype = ctypes.POINTER(drmModeConnector)
    lib.drmModeFreeConnector.argtypes = [ctypes.POINTER(drmModeConnector)]
    lib.drmModeFreeConnector.restype = None
    lib.drmModeGetEncoder.argtypes = [ctypes.c_int, ctypes.c_uint32]
    lib.drmModeGetEncoder.restype = ctypes.POINTER(drmModeEncoder)
    lib.drmModeFreeEncoder.argtypes = [ctypes.POINTER(drmModeEncoder)]
    lib.drmModeFreeEncoder.restype = None
    lib.drmModeGetProperty.argtypes = [ctypes.c_int, ctypes.c_uint32]
    lib.drmModeGetProperty.restype = ctypes.POINTER(drmModeProperty)
    lib.drmModeFreeProperty.argtypes = [ctypes.POINTER(drmModeProperty)]
    lib.drmModeFreeProperty.restype = None
    lib.drmModeAddFB.argtypes = [
        ctypes.c_int,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint8,
        ctypes.c_uint8,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    lib.drmModeAddFB.restype = ctypes.c_int
    lib.drmModeRmFB.argtypes = [ctypes.c_int, ctypes.c_uint32]
    lib.drmModeRmFB.restype = ctypes.c_int
    lib.drmModeSetCrtc.argtypes = [
        ctypes.c_int,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_int,
        ctypes.POINTER(drmModeModeInfo),
    ]
    lib.drmModeSetCrtc.restype = ctypes.c_int
    lib.drmModeDirtyFB.argtypes = [
        ctypes.c_int,
        ctypes.c_uint32,
        ctypes.POINTER(drmModeClip),
        ctypes.c_uint32,
    ]
    lib.drmModeDirtyFB.restype = ctypes.c_int
    lib.drmSetMaster.argtypes = [ctypes.c_int]
    lib.drmSetMaster.restype = ctypes.c_int
    lib.drmDropMaster.argtypes = [ctypes.c_int]
    lib.drmDropMaster.restype = ctypes.c_int
    return lib


def inspect_appletbdrm_card(path: str) -> DrmCard:
    lib = _libdrm()
    fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
    try:
        res = lib.drmModeGetResources(fd)
        if not res:
            raise RuntimeError(f"drmModeGetResources failed on {path}")
        try:
            connector = None
            for index in range(res.contents.count_connectors):
                connector_id = res.contents.connectors[index]
                probed = lib.drmModeGetConnector(fd, connector_id)
                current = lib.drmModeGetConnectorCurrent(fd, connector_id)
                candidate, unused = select_connected_connector(probed, current)
                for option in unused:
                    lib.drmModeFreeConnector(option)
                if candidate is None:
                    continue
                connector = candidate
                break
            if connector is None:
                raise RuntimeError(f"no connected Touch Bar connector on {path}")
            try:
                mode = copy_drm_mode(connector.contents.modes[0])
                rotate90 = False
                for prop_index in range(connector.contents.count_props):
                    prop = lib.drmModeGetProperty(fd, connector.contents.props[prop_index])
                    if not prop:
                        continue
                    try:
                        if prop.contents.name.decode("utf-8", "replace") == "panel orientation":
                            value = int(connector.contents.prop_values[prop_index])
                            rotate90 = value in (2, 3)
                    finally:
                        lib.drmModeFreeProperty(prop)
                return DrmCard(
                    name=Path(path).name,
                    driver="appletbdrm",
                    path=path,
                    hdisplay=int(mode.hdisplay),
                    vdisplay=int(mode.vdisplay),
                    rotate90=rotate90,
                )
            finally:
                lib.drmModeFreeConnector(connector)
        finally:
            lib.drmModeFreeResources(res)
    finally:
        os.close(fd)


@dataclass
class LiveDisplaySession:
    card: DrmCard
    fd: int
    lib: ctypes.CDLL
    fb_id: int
    handle: int
    crtc_id: int
    conn_id: int
    mode: drmModeModeInfo
    mapping: mmap.mmap
    pitch: int
    closed: bool = False

    @property
    def size(self) -> tuple[int, int]:
        return self.card.logical_size

    def present_test_surface(self) -> None:
        self._draw_to_scanout(
            lambda ctx, width, height: draw_test_surface(ctx, width, height),
            "test surface",
        )

    def present_frame(self, frame: RuntimeFrame) -> None:
        self._draw_to_scanout(
            lambda ctx, width, height: draw_runtime_frame(ctx, width, height, frame),
            "runtime frame",
        )

    def _draw_to_scanout(self, draw, description: str) -> None:
        if self.closed:
            raise RuntimeError("display already closed")
        width, height = self.size
        if (width, height) != (NATIVE_WIDTH, NATIVE_HEIGHT):
            raise RuntimeError(f"Touch Bar mode is {width}x{height}, expected {NATIVE_WIDTH}x{NATIVE_HEIGHT}")
        try:
            import cairo
        except ImportError as exc:
            raise RuntimeError(f"python-cairo is required to draw the {description}") from exc
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        ctx = cairo.Context(surface)
        draw(ctx, width, height)
        surface.flush()
        src = surface.get_data()
        stride = surface.get_stride()
        # appletbdrm scanout is physically rotated. The logical 2008x60 image is
        # written into the 60x2008 dumb buffer with a clockwise 90-degree copy.
        buf = memoryview(self.mapping)
        copy_logical_to_physical_scanout(
            memoryview(src),
            src_stride=stride,
            width=width,
            height=height,
            dest=buf,
            dest_pitch=self.pitch,
            rotate90=self.card.rotate90,
        )
        print(
            f"scanout rotate90={self.card.rotate90} pitch={self.pitch} map={self.mapping.size()} mode={self.card.hdisplay}x{self.card.vdisplay}",
            flush=True,
        )
        clip = drmModeClip(0, 0, self.card.hdisplay, self.card.vdisplay)
        dirty = self.lib.drmModeDirtyFB(self.fd, self.fb_id, ctypes.byref(clip), 1)
        conn = ctypes.c_uint32(self.conn_id)
        crtc = self.lib.drmModeSetCrtc(
            self.fd,
            self.crtc_id,
            self.fb_id,
            0,
            0,
            ctypes.byref(conn),
            1,
            ctypes.byref(self.mode),
        )
        print(f"drmModeDirtyFB={dirty} drmModeSetCrtc={crtc}", flush=True)
        if dirty != 0:
            raise RuntimeError(f"drmModeDirtyFB failed while presenting the {description}: {dirty}")
        if crtc != 0:
            raise RuntimeError(f"drmModeSetCrtc failed while presenting the {description}: {crtc}")

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.mapping.close()
        finally:
            try:
                self.lib.drmModeRmFB(self.fd, self.fb_id)
            finally:
                destroy = drm_mode_destroy_dumb(self.handle)
                try:
                    fcntl.ioctl(self.fd, DRM_MODE_DESTROY_DUMB, destroy)
                finally:
                    self.lib.drmDropMaster(self.fd)
                    os.close(self.fd)


def open_appletbdrm_display(card: DrmCard) -> LiveDisplaySession:
    if card.driver != "appletbdrm":
        raise RuntimeError(f"refusing to modeset {card.driver} on {card.path}")
    lib = _libdrm()
    fd = os.open(card.path, os.O_RDWR | os.O_CLOEXEC)
    owned = False
    try:
        if lib.drmSetMaster(fd) != 0:
            raise RuntimeError(f"unable to become DRM master of {card.path}")
        res = lib.drmModeGetResources(fd)
        if not res:
            raise RuntimeError("drmModeGetResources failed")
        try:
            connector = None
            for index in range(res.contents.count_connectors):
                connector_id = res.contents.connectors[index]
                probed = lib.drmModeGetConnector(fd, connector_id)
                current = lib.drmModeGetConnectorCurrent(fd, connector_id)
                candidate, unused = select_connected_connector(probed, current)
                for option in unused:
                    lib.drmModeFreeConnector(option)
                if candidate is None:
                    continue
                connector = candidate
                break
            if connector is None:
                raise RuntimeError("no connected appletbdrm connector")
            try:
                mode = copy_drm_mode(connector.contents.modes[0])
                conn_id = connector.contents.connector_id
                crtc_id = 0
                if connector.contents.encoder_id:
                    encoder = lib.drmModeGetEncoder(fd, connector.contents.encoder_id)
                    if encoder:
                        crtc_id = encoder.contents.crtc_id
                        lib.drmModeFreeEncoder(encoder)
                if not crtc_id and res.contents.count_crtcs:
                    crtc_id = res.contents.crtcs[0]
                create = drm_mode_create_dumb()
                create.width = int(mode.hdisplay)
                create.height = int(mode.vdisplay)
                create.bpp = 32
                fcntl.ioctl(fd, DRM_MODE_CREATE_DUMB, create)
                fb_id = ctypes.c_uint32()
                if lib.drmModeAddFB(fd, mode.hdisplay, mode.vdisplay, 24, 32, create.pitch, create.handle, ctypes.byref(fb_id)):
                    raise RuntimeError("drmModeAddFB failed")
                mapper = drm_mode_map_dumb(create.handle, 0, 0)
                fcntl.ioctl(fd, DRM_MODE_MAP_DUMB, mapper)
                mapping = mmap.mmap(fd, create.size, offset=mapper.offset)
                conn = ctypes.c_uint32(conn_id)
                if lib.drmModeSetCrtc(fd, crtc_id, fb_id.value, 0, 0, ctypes.byref(conn), 1, ctypes.byref(mode)) != 0:
                    raise RuntimeError("drmModeSetCrtc failed")
                live_card = DrmCard(
                    name=card.name,
                    driver=card.driver,
                    path=card.path,
                    hdisplay=card.hdisplay or mode.hdisplay,
                    vdisplay=card.vdisplay or mode.vdisplay,
                    rotate90=card.rotate90 or (mode.hdisplay < mode.vdisplay),
                )
                session = LiveDisplaySession(
                    card=live_card,
                    fd=fd,
                    lib=lib,
                    fb_id=fb_id.value,
                    handle=create.handle,
                    crtc_id=crtc_id,
                    conn_id=conn_id,
                    mode=mode,
                    mapping=mapping,
                    pitch=int(create.pitch),
                )
                owned = True
                return session
            finally:
                lib.drmModeFreeConnector(connector)
        finally:
            lib.drmModeFreeResources(res)
    except Exception:
        if not owned:
            os.close(fd)
        raise
