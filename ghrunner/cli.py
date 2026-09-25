"""Typer CLI surface (PLAN.md §7)."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import typer

from ghrunner import fleet, init_config, secrets, service, watch
from ghrunner.config import DEFAULT_CONFIG_PATH, Config
from ghrunner.github_app import GithubApp

PRIVATE_KEY_FILENAME = "github-app.pem"

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


def _answer(question: init_config.Question, flag_value: str | None):
    """A flag value is validated once and rejected outright; otherwise prompt,
    re-asking until the answer parses.
    """
    if flag_value is not None:
        try:
            return question.parse(flag_value)
        except ValueError as exc:
            flag = "--" + question.key.replace("_", "-")
            typer.secho(f"{flag}: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
    while True:
        raw = typer.prompt(question.prompt, default=question.default())
        try:
            return question.parse(raw)
        except ValueError as exc:
            typer.secho(f"  {exc}", fg=typer.colors.RED, err=True)


def _flag(help: str) -> typer.models.OptionInfo:
    return typer.Option(None, help=f"{help} (prompted for if omitted)")


@app.command()
def init(
    path: Path = typer.Option(
        DEFAULT_CONFIG_PATH, "--path", help="Where to write ghrunner.yaml"
    ),
    app_id: str | None = _flag("GitHub App ID or Client ID"),
    pem: str | None = _flag("GitHub App private key (.pem) to install"),
    repo: str | None = _flag("<owner>/<repo> to register runners against"),
    count: str | None = _flag("Number of runners"),
    name_prefix: str | None = _flag("Runner name prefix"),
    labels: str | None = _flag("Comma-separated runner labels"),
    cpus: str | None = _flag("CPUs per runner"),
    memory: str | None = _flag("Memory per runner, e.g. 6G"),
    dockerfile_dir: str | None = _flag("Runner image build directory"),
    image_tag: str | None = _flag("Runner image tag"),
):
    """Write ghrunner.yaml, asking for each value, and install the App's private key.

    The key is copied next to the config as github-app.pem with mode 0600.
    Nothing is written until every answer is in. Refuses to overwrite an
    existing config.
    """
    path = path.expanduser()
    if path.exists():
        typer.secho(f"{path} already exists, not overwriting", fg=typer.colors.YELLOW)
        raise typer.Exit(1)

    flags = {
        "app_id": app_id,
        "pem": pem,
        "repo": repo,
        "count": count,
        "name_prefix": name_prefix,
        "labels": labels,
        "cpus": cpus,
        "memory": memory,
        "dockerfile_dir": dockerfile_dir,
        "image_tag": image_tag,
    }
    # Check every flag before prompting, so a bad one fails fast.
    answers = {
        q.key: _answer(q, flags[q.key])
        for q in init_config.QUESTIONS
        if flags[q.key] is not None
    }
    for q in init_config.QUESTIONS:
        if q.key not in answers:
            answers[q.key] = _answer(q, None)

    pem_path = answers["pem"]
    key_dest = path.parent / PRIVATE_KEY_FILENAME
    same_file = key_dest.exists() and key_dest.resolve() == pem_path.resolve()
    if key_dest.exists() and not same_file:
        typer.confirm(f"{key_dest} already exists, overwrite it?", abort=True)
    text = init_config.render(answers, key_dest)
    try:
        secrets.install_private_key(pem_path, key_dest)
    except (OSError, ValueError) as exc:
        typer.secho(f"can't install {pem_path}: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    path.write_text(text)

    typer.echo(f"installed private key at {key_dest} (mode 0600)")
    if not same_file:
        typer.echo(
            f"  the original at {pem_path} is no longer needed and can be deleted"
        )
    typer.echo(f"wrote {path}")
    typer.echo("next: `ghrunner config validate`, then `ghrunner up`")


@config_app.command("validate")
def config_validate(path: Path = typer.Option(DEFAULT_CONFIG_PATH, "--path")):
    """Schema check + private key check + GitHub App auth dry-run."""
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
    """Stop + deregister (host-side, via the GitHub App) one or all runners."""
    config = _load_config(path)
    if not all and not name:
        typer.secho("pass --all or --name <runner>", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    fleet.down(config, GithubApp(config), name=name)
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

service_app = typer.Typer(no_args_is_help=True)
app.add_typer(service_app, name="service")


@service_app.command("install")
def service_install(path: Path = typer.Option(DEFAULT_CONFIG_PATH, "--path")):
    """Run `ghrunner watch` as a login service (launchd on macOS, systemd on Linux)."""
    _load_config(path)
    try:
        lines = service.install(path.expanduser().resolve())
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        typer.secho(f"service install failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    for line in lines:
        typer.echo(line)


@service_app.command("uninstall")
def service_uninstall():
    """Stop and remove the `ghrunner watch` service."""
    try:
        lines = service.uninstall()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        typer.secho(f"service uninstall failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    for line in lines:
        typer.echo(line)


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
