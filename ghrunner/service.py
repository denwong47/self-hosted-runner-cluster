"""Installs `ghrunner watch` as a per-user service (PLAN.md §6): a launchd
agent on macOS, a systemd user unit on Linux. Generated rather than shipped as
static templates, because the right values are only known on the host: where
`ghrunner` was installed, and where the `docker` CLI lives. Service managers
start with a minimal PATH, so without the latter every `docker` call fails.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

LAUNCHD_LABEL = "com.ghrunner.watch"
SYSTEMD_UNIT = "ghrunner-watch.service"
BASE_PATH = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
]


def watch_argv(config_path: Path) -> list[str]:
    # The interpreter of whatever venv pipx/uv installed ghrunner into, so the
    # service doesn't depend on where the `ghrunner` shim landed.
    return [sys.executable, "-m", "ghrunner.cli", "watch", "--path", str(config_path)]


def search_path() -> str:
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError(
            "`docker` isn't on PATH -- install Docker Desktop (or Colima) first"
        )
    # Both the symlink's dir and its target's: Docker Desktop's credential
    # helpers sit next to the real binary inside Docker.app.
    dirs = [str(Path(docker).parent), str(Path(docker).resolve().parent), *BASE_PATH]
    return ":".join(dict.fromkeys(dirs))


def launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def launchd_log_path() -> Path:
    return Path.home() / "Library" / "Logs" / "ghrunner" / "watch.log"


def systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / SYSTEMD_UNIT


def render_launchd_plist(argv: list[str], path_env: str, log_path: Path) -> bytes:
    return plistlib.dumps(
        {
            "Label": LAUNCHD_LABEL,
            "ProgramArguments": argv,
            "EnvironmentVariables": {"PATH": path_env, "PYTHONUNBUFFERED": "1"},
            "RunAtLoad": True,
            "KeepAlive": True,
            "StandardOutPath": str(log_path),
            "StandardErrorPath": str(log_path),
        }
    )


def render_systemd_unit(argv: list[str], path_env: str) -> str:
    return f"""[Unit]
Description=ghrunner desync watch loop
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
Environment=PATH={path_env}
Environment=PYTHONUNBUFFERED=1
ExecStart={shlex.join(argv)}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
"""


def install(config_path: Path, platform: str = sys.platform) -> list[str]:
    """Writes and (re)starts the service. Returns lines to show the user."""
    argv = watch_argv(config_path)
    path_env = search_path()
    if platform == "darwin":
        plist, log_path = launchd_plist_path(), launchd_log_path()
        plist.parent.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        plist.write_bytes(render_launchd_plist(argv, path_env, log_path))
        domain = f"gui/{os.getuid()}"
        # Unload any previous version first; fails harmlessly if there isn't one.
        subprocess.run(
            ["launchctl", "bootout", f"{domain}/{LAUNCHD_LABEL}"], capture_output=True
        )
        subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=True)
        return [f"installed {plist}", f"logs: {log_path}"]
    if platform.startswith("linux"):
        unit = systemd_unit_path()
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(render_systemd_unit(argv, path_env))
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", SYSTEMD_UNIT], check=True)
        subprocess.run(["systemctl", "--user", "restart", SYSTEMD_UNIT], check=True)
        return [
            f"installed {unit}",
            f"logs: journalctl --user -u {SYSTEMD_UNIT} -f",
            "to start at boot without logging in: loginctl enable-linger $USER",
        ]
    raise RuntimeError(f"unsupported platform {platform!r} (macOS and Linux only)")


def uninstall(platform: str = sys.platform) -> list[str]:
    if platform == "darwin":
        plist = launchd_plist_path()
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"],
            capture_output=True,
        )
        plist.unlink(missing_ok=True)
        return [f"removed {plist}"]
    if platform.startswith("linux"):
        unit = systemd_unit_path()
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT], check=False
        )
        unit.unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        return [f"removed {unit}"]
    raise RuntimeError(f"unsupported platform {platform!r} (macOS and Linux only)")
