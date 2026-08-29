from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

from .discovery import competing_renderer_names, find_touchbar_usb, list_drm_cards, read_firmware_row
from .live import LiveHost
from .owner import OwnerError, TouchBarOwner
from .restore import restore_firmware_row
from .runtime import PreflightError, SupervisedRuntime, SupervisedRuntimeError
from .types import OBSERVED_BASELINE, firmware_row_restored


def _print_row(row) -> None:
    print(
        json.dumps(
            {
                "usb_configuration": row.usb_configuration,
                "special_key_mode": row.special_key_mode,
                "fn_toggle": row.fn_toggle,
                "autodim": row.autodim,
                "brightness": row.brightness,
                "appletbdrm_loaded": row.appletbdrm_loaded,
            },
            indent=2,
        )
        )


def _in_group(name: str) -> bool:
    try:
        import grp

        gid = grp.getgrnam(name).gr_gid
    except KeyError:
        return False
    return gid in os.getgroups()


def _writable(path: str) -> bool:
    return os.access(path, os.W_OK)


def cmd_status(_args: argparse.Namespace) -> int:
    row = read_firmware_row()
    print("firmware_row")
    _print_row(row)
    print("baseline")
    _print_row(OBSERVED_BASELINE)
    print("competing_renderers", competing_renderer_names() or "none")
    print("drm_cards")
    for card in list_drm_cards():
        print(f"  {card.path} driver={card.driver}")
    usb = find_touchbar_usb()
    config = usb / "bConfigurationValue" if usb else None
    print("permissions")
    print(f"  video_group={_in_group('video')}")
    print(f"  input_group={_in_group('input')}")
    print(f"  usb_config_writable={config.is_file() and os.access(config, os.W_OK) if config else False}")
    print(f"  backlight_writable={_writable('/sys/class/backlight/appletb_backlight/brightness')}")
    print(f"  uinput_writable={_writable('/dev/uinput')}")
    return 0


def cmd_restore(_args: argparse.Namespace) -> int:
    row = restore_firmware_row()
    _print_row(row)
    if not firmware_row_restored(row, OBSERVED_BASELINE):
        return 1
    return 0


def cmd_claim(args: argparse.Namespace) -> int:
    host = LiveHost()
    owner = TouchBarOwner(host)
    stop = {"value": False}

    def handle_stop(_signum: int, _frame: object) -> None:
        stop["value"] = True

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)
    try:
        owner.claim()
    except (OwnerError, OSError, PermissionError) as exc:
        print(f"claim failed: {exc}", file=sys.stderr)
        try:
            restore_firmware_row()
        except Exception as restore_exc:
            print(f"restore after failed claim: {restore_exc}", file=sys.stderr)
        return 1
    print("claimed", sorted(owner.state.claimed))
    print("opened_drm", host.opened_drm_cards)
    print("firmware_row_during_claim")
    _print_row(host.firmware_row())
    deadline = time.monotonic() + args.seconds
    seen: list[str] = []
    rc = 0
    try:
        while time.monotonic() < deadline and not stop["value"]:
            events = owner.drain_touch()
            for event in events:
                line = f"{event.kind} x={event.x} y={event.y}"
                print(line, flush=True)
                seen.append(event.kind)
            time.sleep(0.05)
    finally:
        try:
            owner.release()
        except OwnerError as exc:
            print(f"release failed: {exc}", file=sys.stderr)
            rc = 1
    if rc != 0:
        return rc
    print("released")
    print("firmware_row_after_release")
    _print_row(host.firmware_row())
    if args.require_touch and not {"down", "move", "up"}.issubset(set(seen)):
        print("missing physical touch-down, movement, or release", file=sys.stderr)
        return 2
    return 0


def cmd_idle(args: argparse.Namespace) -> int:
    host = LiveHost()
    owner = TouchBarOwner(host)
    rc = 0
    try:
        owner.claim()
        start = time.process_time()
        wall_start = time.monotonic()
        while time.monotonic() - wall_start < args.seconds:
            owner.drain_touch()
            time.sleep(0.25)
        cpu = time.process_time() - start
        wall = time.monotonic() - wall_start
        percent = (cpu / wall) * 100 if wall else 0.0
        print(json.dumps({"seconds": round(wall, 3), "cpu_seconds": round(cpu, 6), "percent_of_one_core": round(percent, 4)}))
        if percent >= 1.0:
            rc = 2
    except OwnerError as exc:
        print(f"idle measurement failed: {exc}", file=sys.stderr)
        rc = 1
    finally:
        try:
            owner.release()
        except OwnerError as exc:
            print(f"release failed: {exc}", file=sys.stderr)
            try:
                restore_firmware_row()
            except Exception as restore_exc:
                print(f"restore after failed release: {restore_exc}", file=sys.stderr)
            rc = 1
    return rc


def cmd_preflight(_args: argparse.Namespace) -> int:
    runtime = SupervisedRuntime(LiveHost())
    try:
        runtime.preflight()
    except PreflightError as exc:
        print(f"preflight failed: {exc}", file=sys.stderr)
        return 1
    print("preflight ok")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    host = LiveHost()
    runtime = SupervisedRuntime(host, restart_delay=args.restart_delay)
    stop = {"value": False}

    def handle_stop(_signum: int, _frame: object) -> None:
        stop["value"] = True

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)
    restarts = 0
    rc = 0
    try:
        while not stop["value"]:
            try:
                runtime.run_supervised(cycles=1, max_restarts=0)
            except PreflightError as exc:
                if runtime.state.running:
                    rc = 1
                print(f"runtime preflight failed: {exc}", file=sys.stderr)
                break
            except SupervisedRuntimeError as exc:
                print(f"runtime failure: {exc}", file=sys.stderr)
                restarts += 1
                if restarts > args.max_restarts:
                    rc = 1
                    break
                time.sleep(args.restart_delay)
                continue
            restarts = 0
            if not runtime.state.running and not host.graphical_session_ready():
                break
            time.sleep(args.poll_interval)
    finally:
        try:
            runtime.stop()
        except SupervisedRuntimeError as exc:
            print(f"runtime stop failed: {exc}", file=sys.stderr)
            rc = 1
    return rc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TouchSignal Touch Bar owner")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="record the current firmware-row baseline").set_defaults(func=cmd_status)
    sub.add_parser("restore", help="restore USB config 1, special-key mode, Fn, autodim, and brightness").set_defaults(func=cmd_restore)
    sub.add_parser("preflight", help="verify that the runtime may claim the Touch Bar").set_defaults(func=cmd_preflight)
    run = sub.add_parser("run", help="run the supervised Touch Bar owner")
    run.add_argument("--poll-interval", type=float, default=0.05)
    run.add_argument("--restart-delay", type=float, default=1.0)
    run.add_argument("--max-restarts", type=int, default=3)
    run.set_defaults(func=cmd_run)
    claim = sub.add_parser("claim", help="claim appletbdrm, draw the 2008x60 test surface, then restore")
    claim.add_argument("--seconds", type=float, default=20.0)
    claim.add_argument("--require-touch", action="store_true")
    claim.set_defaults(func=cmd_claim)
    idle = sub.add_parser("idle", help="measure idle CPU while the owner holds the bar")
    idle.add_argument("--seconds", type=float, default=300.0)
    idle.set_defaults(func=cmd_idle)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OwnerError, RuntimeError, OSError, PermissionError) as exc:
        command = getattr(args, "command", "command")
        print(f"{command} failed: {exc}", file=sys.stderr)
        try:
            restore_firmware_row()
        except Exception as restore_exc:
            print(f"restore after failed {command}: {restore_exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
