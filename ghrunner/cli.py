"""Typer CLI surface (PLAN.md §7)."""

from __future__ import annotations

import logging
import shutil
import sys
from importlib import resources
from pathlib import Path

import typer

from ghrunner import fleet, watch
from ghrunner.config import DEFAULT_CONFIG_PATH, Config
from ghrunner.github_app import GithubApp

app = typer.Typer(add_completion=False, no_args_is_help=True)
config_app = typer.Typer(no_args_is_help=True)
app.add_typer(config_app, name="config")

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)


def _load_config(path: Path) -> Config:
    try:
        return Config.load(path)
    except Exception as exc:
        typer.secho(f"error loading config: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


@app.command()
def init(
    path: Path = typer.Option(
        DEFAULT_CONFIG_PATH, "--path", help="Where to write ghrunner.yaml"
    ),
):
    """Copy ghrunner.sample.yaml -> config path. Refuses to overwrite."""
    path = path.expanduser()
    if path.exists():
        typer.secho(f"{path} already exists, not overwriting", fg=typer.colors.YELLOW)
        raise typer.Exit(1)
    path.parent.mkdir(parents=True, exist_ok=True)
    sample = resources.files("ghrunner.templates").joinpath("config.sample.yaml")
    shutil.copyfile(str(sample), str(path))
    typer.echo(f"wrote {path} -- edit it, then run `ghrunner config validate`")


@config_app.command("validate")
def config_validate(path: Path = typer.Option(DEFAULT_CONFIG_PATH, "--path")):
    """Schema check + AWS creds check + GitHub App auth dry-run."""
    config = _load_config(path)
    typer.echo(
        f"config OK: {config.fleet.count} runners, prefix={config.fleet.name_prefix!r}, repo={config.repo}"
    )
    github_app = GithubApp(config)
    try:
        github_app.mint_registration_token()
    except Exception as exc:
        typer.secho(f"GitHub App auth failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.secho(
        "GitHub App auth OK (minted a live registration token)", fg=typer.colors.GREEN
    )


@app.command()
def up(
    path: Path = typer.Option(DEFAULT_CONFIG_PATH, "--path"),
    count: int | None = typer.Option(
        None, "--count", help="Override fleet.count for this run"
    ),
):
    """Build image if needed, render compose files, reconcile fleet to desired count."""
    config = _load_config(path)
    if count is not None:
        config.fleet.count = count
    github_app = GithubApp(config)
    fleet.up(config, github_app)
    typer.echo(f"fleet reconciled: {len(config.runner_names())} runners desired")


@app.command()
def down(
    path: Path = typer.Option(DEFAULT_CONFIG_PATH, "--path"),
    all: bool = typer.Option(
        False, "--all", help="Stop every runner under this fleet's prefix"
    ),
    name: str | None = typer.Option(None, "--name", help="Stop just this one runner"),
):
    """Stop + deregister one or all runners."""
    config = _load_config(path)
    if not all and not name:
        typer.secho("pass --all or --name <runner>", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    fleet.down(config, name=name)
    typer.echo("done")


@app.command()
def scale(
    n: int = typer.Argument(..., help="New desired runner count"),
    path: Path = typer.Option(DEFAULT_CONFIG_PATH, "--path"),
):
    """Shorthand for `up --count N`."""
    up(path=path, count=n)


@app.command()
def status(path: Path = typer.Option(DEFAULT_CONFIG_PATH, "--path")):
    """List runner containers + GitHub registration state, side by side."""
    config = _load_config(path)
    github_app = GithubApp(config)
    local = fleet.list_running(config)
    gh_runners = github_app.list_runners()
    names = sorted(set(config.runner_names()) | local | gh_runners.keys())
    typer.echo(f"{'NAME':<24} {'LOCAL':<10} {'GITHUB':<10} {'BUSY':<6}")
    for name in names:
        local_state = "running" if name in local else "-"
        gh_info = gh_runners.get(name)
        gh_state = gh_info.status if gh_info else "-"
        busy = str(gh_info.busy) if gh_info else "-"
        typer.echo(f"{name:<24} {local_state:<10} {gh_state:<10} {busy:<6}")


@app.command()
def logs(
    name: str,
    path: Path = typer.Option(DEFAULT_CONFIG_PATH, "--path"),
    follow: bool = typer.Option(False, "--follow", "-f"),
):
    """docker logs for one runner."""
    import subprocess

    cmd = ["docker", "logs", name]
    if follow:
        cmd.append("--follow")
    subprocess.run(cmd, check=False)


def watch_cmd(
    path: Path = typer.Option(DEFAULT_CONFIG_PATH, "--path"),
    interval: int = typer.Option(watch.DEFAULT_INTERVAL_SECONDS, "--interval"),
):
    """Foreground/supervised desync poll loop (§5)."""
    config = _load_config(path)
    github_app = GithubApp(config)
    typer.echo(f"watching (poll every {interval}s, Ctrl-C to stop)")
    watch.run(config, github_app, interval=interval)


app.command(name="watch")(watch_cmd)

token_app = typer.Typer(no_args_is_help=True)
app.add_typer(token_app, name="token")


@token_app.command("test")
def token_test(path: Path = typer.Option(DEFAULT_CONFIG_PATH, "--path")):
    """Mint one registration token, print masked, confirm the App works."""
    config = _load_config(path)
    github_app = GithubApp(config)
    token = github_app.mint_registration_token()
    masked = token[:4] + "…" + token[-4:] if len(token) > 8 else "…"
    typer.secho(f"minted registration token: {masked}", fg=typer.colors.GREEN)


def main() -> None:
    app()


if __name__ == "__main__":
    sys.exit(main())
