import stat

import pytest
import yaml
from typer.testing import CliRunner

from ghrunner.cli import app

PEM = "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----\n"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    src = tmp_path / "Downloads" / "my app.private-key.pem"
    src.parent.mkdir()
    src.write_text(PEM)
    repo_dir = tmp_path / "self-hosted-runner-cluster"
    (repo_dir / "docker").mkdir(parents=True)
    (repo_dir / "docker" / "Dockerfile").write_text("FROM scratch\n")
    monkeypatch.chdir(repo_dir)
    return src, repo_dir / "docker", tmp_path / "ghrunner" / "ghrunner.yaml"


def test_init_prompts_for_everything(setup):
    src, docker_dir, config_path = setup
    answers = [
        "123456",
        str(src).replace(" ", "\\ "),  # as dragged into macOS Terminal
        "not a repo",  # rejected, asked again
        "https://github.com/my-org/my-repo.git",
        "3",
        "",  # defaults from here on
        "",
        "",
        "",
        "",
        "",
    ]

    result = CliRunner().invoke(
        app, ["init", "--path", str(config_path)], input="\n".join(answers) + "\n"
    )

    assert result.exit_code == 0, result.output
    assert "expected <owner>/<repo>" in result.output
    key = config_path.parent / "github-app.pem"
    assert key.read_text() == PEM
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    raw = yaml.safe_load(config_path.read_text())
    assert raw["github_app"] == {"app_id": "123456", "private_key_path": str(key)}
    assert raw["repo"] == "my-org/my-repo"
    assert raw["fleet"]["count"] == 3
    assert raw["fleet"]["name_prefix"] == "runner"
    assert raw["fleet"]["labels"][:2] == ["self-hosted", "linux"]
    assert raw["fleet"]["resources"] == {"cpus": 4, "memory": "6G"}
    assert raw["image"] == {
        "dockerfile_dir": str(docker_dir.resolve()),
        "tag": "ghrunner/runner:latest",
    }


def test_init_from_flags_without_prompting(setup):
    src, docker_dir, config_path = setup
    result = CliRunner().invoke(
        app,
        [
            "init",
            "--path", str(config_path),
            "--app-id", "Iv23abc",
            "--pem", str(src),
            "--repo", "my-org/my-repo",
            "--count", "2",
            "--name-prefix", "mini",
            "--labels", "self-hosted, linux, arm64, gpu",
            "--cpus", "2.5",
            "--memory", "4G",
            "--dockerfile-dir", str(docker_dir),
            "--image-tag", "mini/runner:1",
        ],
        input="",
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    raw = yaml.safe_load(config_path.read_text())
    assert raw["github_app"]["app_id"] == "Iv23abc"
    assert raw["fleet"]["labels"] == ["self-hosted", "linux", "arm64", "gpu"]
    assert raw["fleet"]["resources"] == {"cpus": 2.5, "memory": "4G"}
    assert raw["image"]["tag"] == "mini/runner:1"


def test_init_rejects_bad_flag_without_writing_anything(setup):
    _, _, config_path = setup
    result = CliRunner().invoke(
        app, ["init", "--path", str(config_path), "--pem", "/nonexistent.pem"]
    )

    assert result.exit_code == 1
    assert "--pem: no file at" in result.output
    assert not config_path.parent.exists()


def test_init_refuses_existing_config(setup):
    _, _, config_path = setup
    config_path.parent.mkdir()
    config_path.write_text("keep me")

    result = CliRunner().invoke(app, ["init", "--path", str(config_path)])

    assert result.exit_code == 1
    assert config_path.read_text() == "keep me"
