"""The questions `ghrunner init` asks, and rendering the answers into
ghrunner.yaml. Each question's parser raises ValueError on bad input, so the
CLI can re-prompt interactively or reject a flag outright.
"""

from __future__ import annotations

import json
import platform
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from string import Template
from typing import Any, Callable

import yaml

from ghrunner import secrets
from ghrunner.config import Config

TEMPLATE_NAME = "config.yaml.tmpl"


def parse_path(raw: str) -> Path:
    """Tolerates pasted/dragged-in paths: surrounding quotes, and the `\\ `
    space escaping macOS Terminal adds when a file is dropped on it.
    """
    raw = raw.strip().strip("'\"").replace("\\ ", " ")
    if not raw:
        raise ValueError("a path is required")
    return Path(raw).expanduser()


def parse_pem(raw: str) -> Path:
    path = parse_path(raw)
    if not path.is_file():
        raise ValueError(f"no file at {path}")
    secrets.check_pem(path.read_text())
    return path


def parse_app_id(raw: str) -> str:
    raw = raw.strip()
    # GitHub accepts either the numeric App ID or the Client ID as the JWT issuer.
    if not (raw.isdigit() or raw.startswith("Iv")):
        raise ValueError(
            "expected the numeric App ID or the Client ID (Iv...), "
            "from the App's settings page"
        )
    return raw


def parse_repo(raw: str) -> str:
    raw = raw.strip().rstrip("/").removesuffix(".git")
    raw = re.sub(r"^(https?://)?github\.com/", "", raw)
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", raw):
        raise ValueError("expected <owner>/<repo> or a github.com URL")
    return raw


def parse_count(raw: str) -> int:
    try:
        count = int(raw)
    except ValueError:
        raise ValueError("expected a whole number") from None
    if count < 1:
        raise ValueError("must be at least 1")
    return count


def parse_name_prefix(raw: str) -> str:
    raw = raw.strip()
    # Becomes the container name prefix, so Docker's naming rules apply.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", raw):
        raise ValueError(
            "letters, digits, '_', '.' and '-' only, starting alphanumeric"
        )
    return raw


def parse_labels(raw: str) -> list[str]:
    labels = [label.strip() for label in raw.split(",") if label.strip()]
    if not labels:
        raise ValueError("at least one label is required")
    return labels


def parse_cpus(raw: str) -> float:
    try:
        cpus = float(raw)
    except ValueError:
        raise ValueError("expected a number, e.g. 4 or 2.5") from None
    if cpus <= 0:
        raise ValueError("must be greater than 0")
    return int(cpus) if cpus.is_integer() else cpus


def parse_memory(raw: str) -> str:
    raw = raw.strip()
    if not re.fullmatch(r"\d+[bkmgBKMG]?", raw):
        raise ValueError("expected a Docker memory size, e.g. 6G or 512m")
    return raw


def parse_dockerfile_dir(raw: str) -> Path:
    path = parse_path(raw).resolve()
    if not (path / "Dockerfile").is_file():
        raise ValueError(f"no Dockerfile in {path}")
    # Stored absolute: `watch` under launchd/systemd doesn't run from the repo.
    return path


def parse_image_tag(raw: str) -> str:
    raw = raw.strip()
    if not raw or " " in raw:
        raise ValueError("expected an image tag, e.g. ghrunner/runner:latest")
    return raw


def default_arch_label() -> str:
    machine = platform.machine().lower()
    return {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64"}.get(machine, machine)


def default_dockerfile_dir() -> str | None:
    candidate = Path.cwd() / "docker"
    return str(candidate) if (candidate / "Dockerfile").is_file() else None


@dataclass
class Question:
    key: str
    prompt: str
    parse: Callable[[str], Any]
    default: Callable[[], str | None] = lambda: None


QUESTIONS = [
    Question("app_id", "GitHub App ID (or Client ID)", parse_app_id),
    Question(
        "pem", "Path to the App's private key (.pem) downloaded from GitHub", parse_pem
    ),
    Question(
        "repo", "Repository the runners register against (owner/repo)", parse_repo
    ),
    Question("count", "Number of runners", parse_count, lambda: "5"),
    Question(
        "name_prefix",
        "Runner name prefix (<prefix>-01, ...)",
        parse_name_prefix,
        lambda: "runner",
    ),
    Question(
        "labels",
        "Runner labels, comma-separated",
        parse_labels,
        lambda: f"self-hosted,linux,{default_arch_label()}",
    ),
    Question("cpus", "CPUs per runner", parse_cpus, lambda: "4"),
    Question("memory", "Memory per runner", parse_memory, lambda: "6G"),
    Question(
        "dockerfile_dir",
        "Runner image build directory (this repo's docker/)",
        parse_dockerfile_dir,
        default_dockerfile_dir,
    ),
    Question(
        "image_tag",
        "Runner image tag",
        parse_image_tag,
        lambda: "ghrunner/runner:latest",
    ),
]


def render(answers: dict[str, Any], private_key_path: Path) -> str:
    """Fills the commented template in; values are JSON-encoded, which is
    valid YAML and takes care of quoting. The result is validated against the
    Config schema before it's returned.
    """
    values = {
        "app_id": answers["app_id"],
        "private_key_path": str(private_key_path),
        "repo": answers["repo"],
        "count": answers["count"],
        "name_prefix": answers["name_prefix"],
        "labels": answers["labels"],
        "cpus": answers["cpus"],
        "memory": answers["memory"],
        "dockerfile_dir": str(answers["dockerfile_dir"]),
        "image_tag": answers["image_tag"],
    }
    template = resources.files("ghrunner.templates").joinpath(TEMPLATE_NAME)
    text = Template(template.read_text()).substitute(
        {k: json.dumps(v) for k, v in values.items()}
    )
    Config.model_validate(yaml.safe_load(text))
    return text
