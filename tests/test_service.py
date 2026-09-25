import plistlib
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ghrunner import service


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A fake home dir, with `docker` as a symlink into an app bundle (like
    Docker Desktop's /usr/local/bin/docker), and subprocess mocked out."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    real_bin = tmp_path / "Docker.app" / "bin"
    real_bin.mkdir(parents=True)
    (real_bin / "docker").write_text("#!/bin/sh\n")
    (real_bin / "docker").chmod(0o755)
    link_bin = tmp_path / "usr-local-bin"
    link_bin.mkdir()
    (link_bin / "docker").symlink_to(real_bin / "docker")
    monkeypatch.setenv("PATH", str(link_bin))
    run = MagicMock()
    monkeypatch.setattr(service.subprocess, "run", run)
    return home, link_bin, real_bin, run


def test_search_path_includes_docker_and_its_real_location(host):
    _, link_bin, real_bin, _ = host
    dirs = service.search_path().split(":")
    assert dirs[:2] == [str(link_bin), str(real_bin.resolve())]
    assert "/usr/bin" in dirs


def test_search_path_requires_docker(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(RuntimeError, match="docker"):
        service.search_path()


def test_install_launchd(host):
    home, link_bin, _, run = host
    config = Path("/cfg/ghrunner.yaml")

    service.install(config, platform="darwin")

    plist_path = home / "Library/LaunchAgents/com.ghrunner.watch.plist"
    plist = plistlib.loads(plist_path.read_bytes())
    assert plist["ProgramArguments"] == [
        sys.executable, "-m", "ghrunner.cli", "watch", "--path", str(config),
    ]  # fmt: skip
    assert plist["EnvironmentVariables"]["PATH"].startswith(str(link_bin))
    assert plist["KeepAlive"] is True and plist["RunAtLoad"] is True
    assert plist["StandardErrorPath"] == str(home / "Library/Logs/ghrunner/watch.log")
    assert (home / "Library/Logs/ghrunner").is_dir()
    commands = [c.args[0][:2] for c in run.call_args_list]
    assert commands == [["launchctl", "bootout"], ["launchctl", "bootstrap"]]


def test_install_systemd(host):
    home, link_bin, _, run = host

    service.install(Path("/cfg/ghrunner.yaml"), platform="linux")

    unit = (home / ".config/systemd/user/ghrunner-watch.service").read_text()
    assert f"Environment=PATH={link_bin}" in unit
    assert (
        f"ExecStart={sys.executable} -m ghrunner.cli watch --path /cfg/ghrunner.yaml"
        in unit
    )
    assert ["systemctl", "--user", "enable", "ghrunner-watch.service"] in [
        c.args[0] for c in run.call_args_list
    ]


def test_uninstall_launchd_removes_plist(host):
    home, _, _, _ = host
    service.install(Path("/cfg/ghrunner.yaml"), platform="darwin")

    service.uninstall(platform="darwin")

    assert not (home / "Library/LaunchAgents/com.ghrunner.watch.plist").exists()
