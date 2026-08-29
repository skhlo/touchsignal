from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path


SERVICE_NAME = "touchsignal-touchbar-owner.service"
ROOT = Path(__file__).resolve().parents[2]
SERVICE_TEMPLATE = ROOT / "systemd/user/touchsignal-touchbar-owner.service.in"
REPO_OWNER_SCRIPT = ROOT / "scripts/touchbar-owner"


def render_user_service(executable: Path) -> str:
    template = SERVICE_TEMPLATE.read_text(encoding="utf-8")
    return template.replace("@TOUCHSIGNAL_OWNER@", str(executable))


def user_service_path(home: Path) -> Path:
    return home / ".config/systemd/user" / SERVICE_NAME


def default_executable() -> Path:
    installed = shutil.which("touchsignal-touchbar-owner")
    if installed:
        return Path(installed)
    if REPO_OWNER_SCRIPT.exists():
        return REPO_OWNER_SCRIPT
    raise RuntimeError("touchsignal-touchbar-owner executable was not found")


def install_user_service(
    home: Path,
    executable: Path,
    run: Callable[..., object] = subprocess.run,
) -> Path:
    target = user_service_path(home)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_user_service(executable), encoding="utf-8")
    run(["systemctl", "--user", "daemon-reload"], check=True)
    run(["systemctl", "--user", "enable", SERVICE_NAME], check=True)
    return target


def remove_user_service(
    home: Path,
    owner_command: Path,
    run: Callable[..., object] = subprocess.run,
) -> Path:
    target = user_service_path(home)
    first_error: Exception | None = None
    try:
        run(["systemctl", "--user", "disable", "--now", SERVICE_NAME], check=False)
        if target.exists():
            target.unlink()
        run(["systemctl", "--user", "daemon-reload"], check=True)
    except Exception as exc:
        first_error = exc
    try:
        run([str(owner_command), "restore"], check=True)
    except Exception:
        if first_error is not None:
            raise first_error
        raise
    if first_error is not None:
        raise first_error
    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install or remove the TouchSignal user service")
    sub = parser.add_subparsers(dest="command", required=True)
    install = sub.add_parser("install", help="install and enable the user service")
    install.add_argument("--home", type=Path, default=Path.home())
    install.add_argument("--executable", type=Path, default=None)
    remove = sub.add_parser("remove", help="disable the user service and restore the firmware row")
    remove.add_argument("--home", type=Path, default=Path.home())
    remove.add_argument("--executable", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    executable = args.executable or default_executable()
    if args.command == "install":
        target = install_user_service(args.home, executable)
        print(target)
        return 0
    if args.command == "remove":
        target = remove_user_service(args.home, executable)
        print(target)
        return 0
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
