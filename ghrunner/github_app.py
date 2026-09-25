"""GitHub App auth chain: JWT -> installation token -> runner registration
token, plus the runners-list/deregister calls `watch.py` uses for desync
detection (see PLAN.md §5). Everything here runs host-side; the private key
and installation tokens never leave the host process.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal, cast

import jwt
import requests

from ghrunner import secrets
from ghrunner.config import Config

API_ROOT = "https://api.github.com"
JWT_TTL_SECONDS = 9 * 60


@dataclass
class RunnerInfo:
    id: int
    name: str
    status: Literal["online", "offline"]
    busy: bool


class GithubApp:
    """Mints registration tokens and queries runner state for one repo.

    Each `up`/`watch` invocation constructs one of these, which fetches the
    private key once and re-derives an installation token on demand -- there
    is no long-lived caching across CLI invocations, by design (§3).
    """

    _config: Config
    _private_key_pem: str | None
    _installation_id: int | None
    _installation_token: str | None
    _installation_token_expires_at: float

    def __init__(self, config: Config):
        self._config = config
        self._private_key_pem: str | None = None
        self._installation_id: int | None = None
        self._installation_token: str | None = None
        self._installation_token_expires_at: float = 0.0

    def _build_jwt(self) -> str:
        if self._private_key_pem is None:
            self._private_key_pem = secrets.read_private_key_pem(
                self._config.github_app
            )
        now = int(time.time())
        payload = {
            "iat": now - 30,
            "exp": now + JWT_TTL_SECONDS,
            "iss": self._config.github_app.app_id,
        }
        return jwt.encode(payload, self._private_key_pem, algorithm="RS256")

    def _get_installation_id(self, app_jwt: str) -> int:
        if self._installation_id is not None:
            return self._installation_id
        resp = requests.get(
            f"{API_ROOT}/app/installations",
            headers=_headers(app_jwt),
            timeout=10,
        )
        resp.raise_for_status()
        owner = self._config.owner.lower()
        for installation in resp.json():
            account = installation.get("account", {})
            if account.get("login", "").lower() == owner:
                self._installation_id = installation["id"]
                return self._installation_id
        raise RuntimeError(
            f"no GitHub App installation found for owner {self._config.owner!r} "
            "-- is the app installed on that account/org?"
        )

    def _get_installation_token(self) -> str:
        if (
            self._installation_token
            and time.time() < self._installation_token_expires_at
        ):
            return self._installation_token
        app_jwt = self._build_jwt()
        installation_id = self._get_installation_id(app_jwt)
        resp = requests.post(
            f"{API_ROOT}/app/installations/{installation_id}/access_tokens",
            headers=_headers(app_jwt),
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
        self._installation_token = body["token"]
        # expires_at is ISO8601; keep a minute of margin before we'd have to re-derive.
        self._installation_token_expires_at = time.time() + 55 * 60
        return self._installation_token

    def mint_registration_token(self) -> str:
        """Mint a fresh, single-use runner registration token (1h TTL)."""
        token = self._get_installation_token()
        resp = requests.post(
            f"{API_ROOT}/repos/{self._config.repo}/actions/runners/registration-token",
            headers=_headers(token),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["token"]

    def list_runners(self) -> dict[str, RunnerInfo]:
        """GitHub's view of this repo's self-hosted runners, keyed by name."""
        token = self._get_installation_token()
        runners: dict[str, RunnerInfo] = {}
        url = f"{API_ROOT}/repos/{self._config.repo}/actions/runners"
        params = {"per_page": 100}
        while url:
            resp = requests.get(url, headers=_headers(token), params=params, timeout=10)
            resp.raise_for_status()
            body = resp.json()
            for r in body.get("runners", []):
                runners[r["name"]] = RunnerInfo(
                    id=r["id"],
                    name=r["name"],
                    status=cast(Literal["online", "offline"], r["status"]),
                    busy=r["busy"],
                )
            url = resp.links.get("next", {}).get("url")
            params = None
        return runners

    def deregister_runner(self, runner_id: int) -> None:
        token = self._get_installation_token()
        resp = requests.delete(
            f"{API_ROOT}/repos/{self._config.repo}/actions/runners/{runner_id}",
            headers=_headers(token),
            timeout=10,
        )
        resp.raise_for_status()


def _headers(bearer_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {bearer_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
