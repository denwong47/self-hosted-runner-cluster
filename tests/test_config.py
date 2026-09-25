import pytest

from ghrunner.config import Config

RAW = {
    "github_app": {
        "app_id": "123456",
        "private_key_path": "/nonexistent/github-app.pem",
    },
    "repo": "my-org/my-repo",
    "fleet": {"count": 3, "name_prefix": "runner", "labels": ["self-hosted", "linux"]},
    "image": {"dockerfile_dir": "docker", "tag": "ghrunner/runner:latest"},
}


def test_load_valid_config():
    config = Config.model_validate(RAW)
    assert config.owner == "my-org"
    assert config.repo_name == "my-repo"
    assert config.runner_names() == ["runner-01", "runner-02", "runner-03"]
    assert config.backend == "docker"


def test_rejects_malformed_repo():
    bad = {**RAW, "repo": "not-a-repo-slug"}
    with pytest.raises(ValueError):
        Config.model_validate(bad)


def test_rejects_zero_count():
    bad = {**RAW, "fleet": {**RAW["fleet"], "count": 0}}
    with pytest.raises(ValueError):
        Config.model_validate(bad)


def test_owns_runner_matches_only_this_fleet():
    config = Config.model_validate(RAW)
    assert config.owns_runner("runner-01")
    assert config.owns_runner("runner-120")
    assert not config.owns_runner("runner-gpu-01")
    assert not config.owns_runner("other-01")
    assert not config.owns_runner("runner-")
