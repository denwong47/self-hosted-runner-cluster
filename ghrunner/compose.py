"""Renders the shared `runner-template.yml` and thin per-runner compose files
that `extends` it (PLAN.md §4). Docker/Compose owns restart + health from
here on; `ghrunner` only ever renders files and shells out to `docker compose`.
"""

from __future__ import annotations

import stat
from pathlib import Path

import yaml

from ghrunner.config import Config

TEMPLATE_FILENAME = "runner-template.yml"


def work_dir(config: Config) -> Path:
    d = Path(config.compose_work_dir).expanduser()
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def render_template(config: Config) -> Path:
    """Writes the shared service definition every runner's compose file extends."""
    doc = {
        "services": {
            "runner": {
                "build": str(Path(config.image.dockerfile_dir).expanduser().resolve()),
                "image": config.image.tag,
                "restart": "unless-stopped",
                "logging": {
                    "driver": "json-file",
                    "options": {"max-size": "10m", "max-file": "3"},
                },
                "healthcheck": {
                    "test": ["CMD", "pgrep", "-f", "run.sh"],
                    "interval": "30s",
                    "timeout": "10s",
                    "retries": 3,
                    "start_period": "40s",
                },
                "mem_limit": config.fleet.resources.memory,
                "cpus": config.fleet.resources.cpus,
            }
        }
    }
    path = work_dir(config) / TEMPLATE_FILENAME
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return path


def runner_compose_path(config: Config, name: str) -> Path:
    return work_dir(config) / f"docker-compose.{name}.yml"


def render_runner(config: Config, name: str, reg_token: str) -> Path:
    """Renders one runner's compose file. Contains REG_TOKEN in plaintext, so
    the file (and its parent dir) are kept 0600/0700 -- it's the same
    short-lived, single-use token `start.sh` would otherwise receive via
    `docker run -e`, just handed to Compose instead.
    """
    render_template(config)
    doc = {
        "services": {
            "runner": {
                "extends": {"file": TEMPLATE_FILENAME, "service": "runner"},
                "container_name": name,
                "environment": {
                    "NAME": name,
                    "REPO": config.repo,
                    "LABELS": ",".join(config.fleet.labels),
                    "REG_TOKEN": reg_token,
                    "WORK_DIR": config.fleet.work_dir,
                    "EPHEMERAL": str(config.fleet.ephemeral).lower(),
                    "DISABLE_AUTO_UPDATE": str(
                        config.fleet.disable_auto_update
                    ).lower(),
                },
            }
        }
    }
    path = runner_compose_path(config, name)
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return path
