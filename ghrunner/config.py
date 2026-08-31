"""Pydantic config models + YAML loader for ghrunner.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

DEFAULT_CONFIG_PATH = Path("~/.config/ghrunner/ghrunner.yaml").expanduser()
DEFAULT_COMPOSE_WORK_DIR = Path("~/.local/state/ghrunner/compose").expanduser()


class PrivateKeyS3(BaseModel):
    bucket: str
    key: str


class GithubAppConfig(BaseModel):
    app_id: str
    client_id: str | None = None
    client_secret_ssm_param: str | None = None
    private_key_s3: PrivateKeyS3
    aws_region: str = "us-east-1"


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
