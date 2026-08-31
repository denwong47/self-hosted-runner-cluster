"""Reconciles desired runner count against what's actually running, by
shelling out to `docker` / `docker compose` (PLAN.md §6). Restart and health
are Compose's job (see compose.py's template); this module only ever
starts/stops whole runners.
"""

from __future__ import annotations

import logging
import subprocess

from ghrunner import compose
from ghrunner.config import Config
from ghrunner.github_app import GithubApp

log = logging.getLogger("ghrunner.fleet")


def list_running(config: Config) -> set[str]:
    """Names of currently-running containers under this fleet's prefix."""
    prefix = config.fleet.name_prefix
    result = subprocess.run(
        ["docker", "ps", "--filter", f"name=^{prefix}-", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    names = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    return names


def start_runner(
    config: Config, github_app: GithubApp, name: str, force_recreate: bool = False
) -> None:
    reg_token = github_app.mint_registration_token()
    compose_file = compose.render_runner(config, name, reg_token)
    cmd = [
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "-p",
        name,
        "up",
        "-d",
        "--build",
    ]
    if force_recreate:
        cmd.append("--force-recreate")
    log.info("starting runner %s", name)
    subprocess.run(cmd, check=True)


def stop_runner(config: Config, name: str) -> None:
    compose_file = compose.runner_compose_path(config, name)
    log.info("stopping runner %s", name)
    if compose_file.exists():
        subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "-p", name, "down"],
            check=True,
        )
        compose_file.unlink(missing_ok=True)
    else:
        # Compose file already gone (e.g. rendered by a prior CLI version/run) --
        # fall back to removing the container directly so `down` still works.
        subprocess.run(["docker", "rm", "-f", name], check=False)


def force_recreate_runner(config: Config, github_app: GithubApp, name: str) -> None:
    """Used by watch.py to heal a GitHub-registration desync (§5): mints a
    fresh registration token and recreates the container under the same name.
    """
    start_runner(config, github_app, name, force_recreate=True)


def up(config: Config, github_app: GithubApp) -> None:
    desired = set(config.runner_names())
    running = list_running(config)
    for name in sorted(desired - running):
        start_runner(config, github_app, name)
    for name in sorted(running - desired):
        stop_runner(config, name)


def down(config: Config, name: str | None = None) -> None:
    if name is not None:
        stop_runner(config, name)
        return
    for existing in sorted(list_running(config)):
        stop_runner(config, existing)
