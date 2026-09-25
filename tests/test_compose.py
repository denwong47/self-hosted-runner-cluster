import stat

import yaml

from ghrunner import compose
from ghrunner.config import Config


def make_config(tmp_path):
    return Config.model_validate(
        {
            "github_app": {
                "app_id": "1",
                "private_key_path": str(tmp_path / "github-app.pem"),
            },
            "repo": "my-org/my-repo",
            "fleet": {"count": 2, "name_prefix": "runner", "labels": ["self-hosted"]},
            "image": {
                "dockerfile_dir": str(tmp_path / "docker"),
                "tag": "ghrunner/runner:latest",
            },
            "compose_work_dir": str(tmp_path / "compose"),
        }
    )


def test_render_runner_extends_template(tmp_path):
    config = make_config(tmp_path)
    path = compose.render_runner(config, "runner-01", "faketoken")

    assert path.exists()
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600

    doc = yaml.safe_load(path.read_text())
    service = doc["services"]["runner"]
    assert service["extends"] == {
        "file": compose.TEMPLATE_FILENAME,
        "service": "runner",
    }
    assert service["container_name"] == "runner-01"
    assert service["environment"]["REG_TOKEN"] == "faketoken"
    assert service["environment"]["REPO"] == "my-org/my-repo"
    assert service["environment"]["LABELS"] == "self-hosted"

    template_path = path.parent / compose.TEMPLATE_FILENAME
    assert template_path.exists()
    template_doc = yaml.safe_load(template_path.read_text())
    assert template_doc["services"]["runner"]["restart"] == "unless-stopped"
    assert template_doc["services"]["runner"]["healthcheck"]["test"] == [
        "CMD",
        "pgrep",
        "-f",
        "run.sh",
    ]
