"""Pydantic config models + YAML loader for ghrunner.yaml."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

DEFAULT_CONFIG_PATH = Path("~/.config/ghrunner/ghrunner.yaml").expanduser()
DEFAULT_PRIVATE_KEY_PATH = Path("~/.config/ghrunner/github-app.pem").expanduser()
DEFAULT_COMPOSE_WORK_DIR = Path("~/.local/state/ghrunner/compose").expanduser()


class GithubAppConfig(BaseModel):
    app_id: str
    private_key_path: str = str(DEFAULT_PRIVATE_KEY_PATH)


class Resources(BaseModel):
    cpus: float = 4
    memory: str = "6G"


class FleetConfig(BaseModel):
    count: int = Field(ge=1)
    name_prefix: str
    labels: list[str] = Field(default_factory=list)
    work_dir: str = "_work"
    ephemeral: bool = False
    disable_auto_update: bool = False
    resources: Resources = Field(default_factory=Resources)


class ImageConfig(BaseModel):
    dockerfile_dir: str
    tag: str


class Config(BaseModel):
    github_app: GithubAppConfig
    repo: str
    fleet: FleetConfig
    image: ImageConfig
    backend: Literal["docker"] = "docker"
    compose_work_dir: str = str(DEFAULT_COMPOSE_WORK_DIR)

    @field_validator("repo")
    @classmethod
    def _repo_is_owner_slash_repo(cls, v: str) -> str:
        if "/" not in v or v.startswith("/") or v.endswith("/"):
            raise ValueError(f"repo must be in '<owner>/<repo>' form, got {v!r}")
        return v

    @property
    def owner(self) -> str:
        return self.repo.split("/", 1)[0]

    @property
    def repo_name(self) -> str:
        return self.repo.split("/", 1)[1]

    def owns_runner(self, name: str) -> bool:
        """True if `name` is one of this fleet's runners (`<prefix>-NN`), at any
        count. Other runners registered to the same repo -- other hosts, other
        prefixes -- are never ours to recreate or deregister.
        """
        return (
            re.fullmatch(rf"{re.escape(self.fleet.name_prefix)}-\d+", name) is not None
        )

    def runner_names(self) -> list[str]:
        return [
            f"{self.fleet.name_prefix}-{i:02d}" for i in range(1, self.fleet.count + 1)
        ]

    @classmethod
    def load(cls, path: Path | str = DEFAULT_CONFIG_PATH) -> "Config":
        path = Path(path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"no config at {path} -- run `ghrunner init` first")
        raw = yaml.safe_load(path.read_text()) or {}
        return cls.model_validate(raw)
