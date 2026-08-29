from __future__ import annotations

import ctypes
import ctypes.util
import fcntl
import mmap
import os
from dataclasses import dataclass
from pathlib import Path

from .types import DrmCard, NATIVE_HEIGHT, NATIVE_WIDTH

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
                candidate = lib.drmModeGetConnectorCurrent(fd, connector_id) or lib.drmModeGetConnector(
                    fd, connector_id
                )
                if not candidate:
                    continue
                if (
                    candidate.contents.connection == DRM_MODE_CONNECTED
                    and candidate.contents.count_modes > 0
                ):
                    connector = candidate
                    break
                lib.drmModeFreeConnector(candidate)
            if connector is None:
                raise RuntimeError(f"no connected Touch Bar connector on {path}")
            try:
                mode = connector.contents.modes[0]
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
    closed: bool = False

    @property
    def size(self) -> tuple[int, int]:
        return self.card.logical_size

    def present_test_surface(self) -> None:
        if self.closed:
            raise RuntimeError("display already closed")
        width, height = self.size
        if (width, height) != (NATIVE_WIDTH, NATIVE_HEIGHT):
            raise RuntimeError(f"Touch Bar mode is {width}x{height}, expected {NATIVE_WIDTH}x{NATIVE_HEIGHT}")
        try:
            import cairo
        except ImportError as exc:
            raise RuntimeError("python-cairo is required to draw the test surface") from exc
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        ctx = cairo.Context(surface)
        ctx.set_source_rgb(0.04, 0.05, 0.07)
        ctx.paint()
        ctx.set_source_rgb(0.16, 0.45, 0.82)
        ctx.rectangle(24, 8, 112, 44)
        ctx.fill()
        ctx.set_source_rgb(0.93, 0.95, 0.97)
        ctx.select_font_face("sans-serif")
        ctx.set_font_size(18)
        ctx.move_to(152, 38)
        ctx.show_text("TouchSignal owner spike 2170x60")
        ctx.set_source_rgb(0.82, 0.28, 0.28)
        ctx.rectangle(width - 220, 8, 196, 44)
        ctx.fill()
        ctx.set_source_rgb(1, 1, 1)
        ctx.move_to(width - 204, 38)
        ctx.show_text("touch here")
        surface.flush()
        src = surface.get_data()
        stride = surface.get_stride()
        # appletbdrm scanout is physically rotated. The logical 2170x60 image is
        # written into the 60x2170 dumb buffer with a clockwise 90-degree copy.
        buf = memoryview(self.mapping)
        if self.card.rotate90:
            physical_h = self.card.vdisplay or height
            pitch = self.mapping.size() // physical_h
            for y in range(height):
                for x in range(width):
                    px = src[y * stride + x * 4 : y * stride + x * 4 + 4]
                    dest_x = height - 1 - y
                    dest_y = x
                    offset = dest_y * pitch + dest_x * 4
                    buf[offset : offset + 4] = px
        else:
            buf[: surface.get_height() * stride] = src
        clip = drmModeClip(0, 0, self.card.hdisplay, self.card.vdisplay)
        if self.lib.drmModeDirtyFB(self.fd, self.fb_id, ctypes.byref(clip), 1) != 0:
            conn = ctypes.c_uint32(self.conn_id)
            self.lib.drmModeSetCrtc(
                self.fd,
                self.crtc_id,
                self.fb_id,
                0,
                0,
                ctypes.byref(conn),
                1,
                ctypes.byref(self.mode),
            )

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
                candidate = lib.drmModeGetConnectorCurrent(fd, connector_id) or lib.drmModeGetConnector(
                    fd, connector_id
                )
                if not candidate:
                    continue
                if (
                    candidate.contents.connection == DRM_MODE_CONNECTED
                    and candidate.contents.count_modes > 0
                ):
                    connector = candidate
                    break
                lib.drmModeFreeConnector(candidate)
            if connector is None:
                raise RuntimeError("no connected appletbdrm connector")
            try:
                mode = connector.contents.modes[0]
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
