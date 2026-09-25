from unittest.mock import MagicMock

import pytest

from ghrunner import watch
from ghrunner.config import Config
from ghrunner.github_app import RunnerInfo


@pytest.fixture
def config():
    return Config.model_validate(
        {
            "github_app": {"app_id": "1"},
            "repo": "my-org/my-repo",
            "fleet": {"count": 2, "name_prefix": "runner"},
            "image": {"dockerfile_dir": "docker", "tag": "ghrunner/runner:latest"},
        }
    )


@pytest.fixture
def fleet_mod(monkeypatch):
    monkeypatch.setattr(watch.fleet, "list_running", MagicMock(return_value=set()))
    monkeypatch.setattr(watch.fleet, "force_recreate_runner", MagicMock())
    return watch.fleet


def test_heals_local_running_but_gh_offline_after_threshold(config, fleet_mod):
    fleet_mod.list_running.return_value = {"runner-01"}
    github_app = MagicMock()
    github_app.list_runners.return_value = {
        "runner-01": RunnerInfo(id=1, name="runner-01", status="offline", busy=False)
    }

    counts: dict[str, int] = {}
    for _ in range(3):
        watch._poll_once(config, github_app, counts, offline_threshold=3)

    fleet_mod.force_recreate_runner.assert_called_once_with(
        config, github_app, "runner-01"
    )


def test_deregisters_stale_github_entry_with_no_local_container(config, fleet_mod):
    github_app = MagicMock()
    github_app.list_runners.return_value = {
        "runner-02": RunnerInfo(id=2, name="runner-02", status="online", busy=False)
    }

    watch._poll_once(config, github_app, {}, offline_threshold=3)

    github_app.deregister_runner.assert_called_once_with(2)


def test_ignores_runners_outside_this_fleet(config, fleet_mod):
    github_app = MagicMock()
    github_app.list_runners.return_value = {
        "other-host-01": RunnerInfo(
            id=3, name="other-host-01", status="online", busy=False
        ),
        "runner-gpu-01": RunnerInfo(
            id=4, name="runner-gpu-01", status="offline", busy=False
        ),
    }

    watch._poll_once(config, github_app, {}, offline_threshold=1)

    github_app.deregister_runner.assert_not_called()
    fleet_mod.force_recreate_runner.assert_not_called()
