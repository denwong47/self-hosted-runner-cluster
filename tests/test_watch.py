from unittest.mock import MagicMock

from ghrunner import watch
from ghrunner.github_app import RunnerInfo


def test_heals_local_running_but_gh_offline_after_threshold():
    fleet_mod = watch.fleet
    original_force_recreate = fleet_mod.force_recreate_runner
    original_list_running = fleet_mod.list_running
    try:
        fleet_mod.list_running = MagicMock(return_value={"runner-01"})
        fleet_mod.force_recreate_runner = MagicMock()

        github_app = MagicMock()
        github_app.list_runners.return_value = {
            "runner-01": RunnerInfo(
                id=1, name="runner-01", status="offline", busy=False
            )
        }

        counts: dict[str, int] = {}
        for _ in range(3):
            watch._poll_once(
                config=None,
                github_app=github_app,
                offline_counts=counts,
                offline_threshold=3,
            )

        fleet_mod.force_recreate_runner.assert_called_once_with(
            None, github_app, "runner-01"
        )
    finally:
        fleet_mod.force_recreate_runner = original_force_recreate
        fleet_mod.list_running = original_list_running


def test_deregisters_stale_github_entry_with_no_local_container():
    fleet_mod = watch.fleet
    original_list_running = fleet_mod.list_running
    try:
        fleet_mod.list_running = MagicMock(return_value=set())

        github_app = MagicMock()
        github_app.list_runners.return_value = {
            "runner-02": RunnerInfo(id=2, name="runner-02", status="online", busy=False)
        }

        watch._poll_once(
            config=None, github_app=github_app, offline_counts={}, offline_threshold=3
        )

        github_app.deregister_runner.assert_called_once_with(2)
    finally:
        fleet_mod.list_running = original_list_running
