"""Desync poll loop (PLAN.md §5). Docker/Compose already handles crash
restarts and HEALTHCHECK -- this loop only catches GitHub's registration
view drifting from what's actually running locally, which Compose can't see.
"""

from __future__ import annotations

import logging
import time

from ghrunner import fleet
from ghrunner.config import Config
from ghrunner.github_app import GithubApp

log = logging.getLogger("ghrunner.watch")

DEFAULT_INTERVAL_SECONDS = 60
# Consecutive polls (~= interval * threshold) a desync must persist before
# healing. Applies to both directions: after a reboot, GitHub still lists the
# runners for the few seconds before Docker has restarted their containers.
DEFAULT_OFFLINE_THRESHOLD = 3


def run(
    config: Config,
    github_app: GithubApp,
    interval: int = DEFAULT_INTERVAL_SECONDS,
    offline_threshold: int = DEFAULT_OFFLINE_THRESHOLD,
) -> None:
    offline_counts: dict[str, int] = {}
    missing_counts: dict[str, int] = {}
    while True:
        try:
            _poll_once(
                config, github_app, offline_counts, offline_threshold, missing_counts
            )
        except Exception:
            log.exception("watch poll failed, will retry next interval")
        time.sleep(interval)


def _poll_once(
    config: Config,
    github_app: GithubApp,
    offline_counts: dict[str, int],
    offline_threshold: int,
    missing_counts: dict[str, int],
) -> None:
    # Only this fleet's runners -- anything else registered to the repo (other
    # hosts, other prefixes) is left alone.
    gh_runners = {
        name: info
        for name, info in github_app.list_runners().items()
        if config.owns_runner(name)
    }
    local = fleet.list_running(config)

    for name, info in gh_runners.items():
        if name not in local:
            continue
        if info.status == "offline":
            offline_counts[name] = offline_counts.get(name, 0) + 1
            if offline_counts[name] >= offline_threshold:
                log.warning(
                    "desync: %s running locally but offline in GitHub for %d polls -- recreating",
                    name,
                    offline_counts[name],
                )
                fleet.force_recreate_runner(config, github_app, name)
                offline_counts[name] = 0
        else:
            offline_counts[name] = 0

    # Running locally with no GitHub registration at all -- recreate to re-register.
    for name in local - gh_runners.keys():
        log.warning(
            "desync: %s running locally but not registered in GitHub -- recreating",
            name,
        )
        fleet.force_recreate_runner(config, github_app, name)
        offline_counts.pop(name, None)

    # Registered in GitHub with nothing running locally -- stale registration,
    # clean it up once it's persisted (not just a container mid-restart).
    for name in list(missing_counts):
        if name in local or name not in gh_runners:
            del missing_counts[name]
    for name, info in gh_runners.items():
        if name in local:
            continue
        offline_counts.pop(name, None)
        missing_counts[name] = missing_counts.get(name, 0) + 1
        if missing_counts[name] >= offline_threshold:
            log.warning(
                "stale registration: %s in GitHub but not running locally for %d polls -- deregistering",
                name,
                missing_counts[name],
            )
            github_app.deregister_runner(info.id)
            del missing_counts[name]
