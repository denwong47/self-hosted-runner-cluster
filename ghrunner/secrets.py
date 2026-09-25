"""Reads the GitHub App private key from a local file. Runs host-side only --
never called from inside a runner container. `ghrunner init` installs the key
at `github_app.private_key_path` with mode 0600; anything looser is refused.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from ghrunner.config import GithubAppConfig

PEM_HEADER = "-----BEGIN"


def check_pem(text: str) -> None:
    if not text.lstrip().startswith(PEM_HEADER):
        raise ValueError(
            "not a PEM file (expected a '-----BEGIN ... PRIVATE KEY-----' header)"
        )


def install_private_key(src: Path, dest: Path) -> None:
    """Copies the PEM at `src` to `dest`, created 0600 in a 0700 directory."""
    text = src.expanduser().read_text()
    check_pem(text)
    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    # O_CREAT's mode is ignored if `dest` already existed.
    dest.chmod(stat.S_IRUSR | stat.S_IWUSR)


def read_private_key_pem(cfg: GithubAppConfig) -> str:
    path = Path(cfg.private_key_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(
            f"no GitHub App private key at {path} -- run `ghrunner init` or set "
            "github_app.private_key_path"
        )
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise PermissionError(
            f"{path} has mode {mode:04o}, refusing to use it -- run `chmod 600 {path}`"
        )
    text = path.read_text()
    check_pem(text)
    return text
