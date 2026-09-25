import stat

import pytest

from ghrunner import secrets
from ghrunner.config import GithubAppConfig

PEM = "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----\n"


def test_install_private_key_copies_with_0600(tmp_path):
    src = tmp_path / "downloaded.pem"
    src.write_text(PEM)
    src.chmod(0o644)
    dest = tmp_path / "config" / "github-app.pem"

    secrets.install_private_key(src, dest)

    assert dest.read_text() == PEM
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    assert stat.S_IMODE(dest.parent.stat().st_mode) == 0o700


def test_install_private_key_tightens_existing_dest(tmp_path):
    src = tmp_path / "downloaded.pem"
    src.write_text(PEM)
    dest = tmp_path / "github-app.pem"
    dest.write_text("old")
    dest.chmod(0o644)

    secrets.install_private_key(src, dest)

    assert stat.S_IMODE(dest.stat().st_mode) == 0o600


def test_install_private_key_rejects_non_pem(tmp_path):
    src = tmp_path / "notes.txt"
    src.write_text("hello")
    dest = tmp_path / "github-app.pem"

    with pytest.raises(ValueError):
        secrets.install_private_key(src, dest)
    assert not dest.exists()


def test_read_private_key_refuses_loose_permissions(tmp_path):
    key = tmp_path / "github-app.pem"
    key.write_text(PEM)
    key.chmod(0o644)
    cfg = GithubAppConfig(app_id="1", private_key_path=str(key))

    with pytest.raises(PermissionError):
        secrets.read_private_key_pem(cfg)

    key.chmod(0o600)
    assert secrets.read_private_key_pem(cfg) == PEM
