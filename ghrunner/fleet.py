"""Reconciles desired runner count against what's actually running, by
shelling out to `docker` / `docker compose` (PLAN.md §6). Restart and health
are Compose's job (see compose.py's template); this module only ever
starts/stops whole runners.
"""

from __future__ import annotations

import logging
import subprocess

import requests

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
    # Docker's name filter is a regex prefix match, so `runner-gpu-01` would
    # match prefix `runner`; narrow to exactly `<prefix>-NN`.
    names = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    return {name for name in names if config.owns_runner(name)}


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


def stop_runner(config: Config, github_app: GithubApp, name: str) -> None:
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
    _deregister(github_app, name)


def _deregister(github_app: GithubApp, name: str) -> None:
    """Removes the runner's GitHub registration from the host side. The
    container can't do this itself on shutdown: `config.sh remove` needs a
    removal token, which only the host (holding the App key) can mint.
    """
    info = github_app.list_runners().get(name)
    if info is None:
        return
    try:
        github_app.deregister_runner(info.id)
        log.info("deregistered runner %s from GitHub", name)
    except requests.HTTPError as exc:
        # e.g. still marked busy; `ghrunner watch` retries once it's offline.
        log.warning(
            "couldn't deregister %s from GitHub (%s), leaving it to watch", name, exc
        )


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
        stop_runner(config, github_app, name)


def down(config: Config, github_app: GithubApp, name: str | None = None) -> None:
    if name is not None:
        stop_runner(config, github_app, name)
        return
    for existing in sorted(list_running(config)):
        stop_runner(config, github_app, existing)
