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
DEFAULT_OFFLINE_THRESHOLD = (
    3  # consecutive polls, ~= interval * threshold before healing
)


def run(
    config: Config,
    github_app: GithubApp,
    interval: int = DEFAULT_INTERVAL_SECONDS,
    offline_threshold: int = DEFAULT_OFFLINE_THRESHOLD,
) -> None:
    offline_counts: dict[str, int] = {}
    while True:
        try:
            _poll_once(config, github_app, offline_counts, offline_threshold)
        except Exception:
            log.exception("watch poll failed, will retry next interval")
        time.sleep(interval)


def _poll_once(
    config: Config,
    github_app: GithubApp,
    offline_counts: dict[str, int],
    offline_threshold: int,
) -> None:
    gh_runners = github_app.list_runners()
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

    # Registered in GitHub with nothing running locally -- stale registration, clean it up.
    for name, info in gh_runners.items():
        if name not in local:
            log.warning(
                "stale registration: %s in GitHub but not running locally -- deregistering",
                name,
            )
            github_app.deregister_runner(info.id)
            offline_counts.pop(name, None)
