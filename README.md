# `ghrunner`

> [!WARNING]
> This is currently complete AI Slop.
>
> I am waiting for a fresh Mac Mini M6 to test this setup, have no expectation whatsoever
> on this setup until I have tested it on the new hardware.

A CLI that manages a fleet of self-hosted GitHub Actions runners as Docker Compose services,
with registration tokens minted on the fly from a GitHub App instead of pasted by hand.

Replaces a single-instance `docker compose up` + manually-copied `REG_TOKEN` workflow with:
`ghrunner up` to bring N runners to the desired count, and `ghrunner watch` to keep GitHub's
registration state in sync with what's actually running.

See [`PLAN.md`](PLAN.md) for the full design rationale. This README is the "how do I use it"
and "what does each piece actually do" version.

## How it works, briefly

- **You never touch a registration token.** `ghrunner` holds a GitHub App private key in a local
  `0600` file (host-side only, never inside a container). On every `up`/`watch` action it
  exchanges that for a fresh, single-use runner registration token per runner
  ([`github_app.py`](ghrunner/github_app.py)). No cloud provider is involved; the host only needs
  to reach `api.github.com`.
- **Docker Compose owns runtime lifecycle.** `ghrunner` doesn't run its own supervisor loop for
  crash-restart or health polling — it renders a shared `runner-template.yml` (with
  `restart: unless-stopped` and a `HEALTHCHECK`) plus one thin `docker-compose.<name>.yml` per
  runner that `extends` it ([`compose.py`](ghrunner/compose.py)), then shells out to
  `docker compose up -d`. Docker does the restarting.
- **`ghrunner` only tracks one thing Compose can't see: registration desync.** A runner container
  can be up and healthy locally while GitHub's side thinks it's offline or has removed it (or vice
  versa) — e.g. an admin removed it from GitHub's UI, or a container went away without
  `ghrunner down` deregistering it. `ghrunner watch` polls the GitHub API and the local container
  list and heals the difference ([`watch.py`](ghrunner/watch.py)). It only touches runners named
  `<name_prefix>-NN`; other runners registered to the same repo (other hosts, other prefixes)
  are left alone.
- **Fleet = count + name prefix.** `fleet.count: 5` and `fleet.name_prefix: runner` means
  `runner-01 .. runner-05`, all sharing the same labels/resources. `ghrunner up`/`down`/`scale`
  diff this desired set against what's actually running ([`fleet.py`](ghrunner/fleet.py)).

## Install

```
pipx install .
# or: uv tool install .
```

Requires Docker + the Compose plugin (`docker compose version`) on the host.

## Setup

1. **Create a GitHub App** (or use an existing one) with repo permission
   `Administration: Read & write` (needed to mint runner registration tokens and list/remove
   runners). Install it on the org/account that owns your target repo.
2. **Generate a private key** for the App and download the `.pem`.
3. **Initialize the config** from the repo checkout (so the `docker/` default is right):

   ```
   ghrunner init
   ```

   This asks for each setting in turn: App ID, the downloaded `.pem`, repo (`owner/repo` or a
   GitHub URL), runner count, name prefix, labels, per-runner CPUs/memory, the image build
   directory and tag. Everything after the repo has a default, and bad answers are re-asked.
   Any answer can be passed as a flag instead (`--app-id`, `--pem`, `--repo`, `--count`, ...;
   see `ghrunner init --help`), so it also runs without prompts.

   Once every answer is in, it copies the key to `~/.config/ghrunner/github-app.pem` with mode
   `0600` (the downloaded original can then be deleted) and writes
   `~/.config/ghrunner/ghrunner.yaml`. The image build directory is stored as an absolute path,
   so `ghrunner watch` works under launchd/systemd, which don't run from the repo. `init` refuses
   to overwrite an existing config; to redo it, delete the file or edit it directly.

4. **Validate it end-to-end** (schema + private key + a live GitHub App auth round-trip that mints
   a real registration token):

   ```
   ghrunner config validate
   ```

## Day-to-day usage

```
ghrunner up                  # build the image if needed, bring the fleet to fleet.count
ghrunner scale 8             # shorthand for `ghrunner up --count 8`
ghrunner status              # local container state next to GitHub's registration state
ghrunner logs runner-03 -f   # docker logs, follow
ghrunner down --name runner-03   # stop + deregister (from the host) just one runner
ghrunner down --all          # tear down the whole fleet
```

`ghrunner up` is idempotent — re-running it only starts what's missing and stops what's extra
relative to `fleet.count`; it won't touch runners that are already correctly up.

### Keeping registration in sync (`ghrunner watch`)

```
ghrunner watch                # foreground; Ctrl-C to stop
ghrunner watch --interval 30  # poll more often than the 60s default
```

Run this continuously so it survives reboots — templates for both are in
[`ghrunner/templates/`](ghrunner/templates/):

- **systemd:** copy `ghrunner-watch.service` to `~/.config/systemd/user/`, then
  `systemctl --user enable --now ghrunner-watch`.
- **launchd:** copy `com.ghrunner.watch.plist` to `~/Library/LaunchAgents/`, then
  `launchctl load ~/Library/LaunchAgents/com.ghrunner.watch.plist`.

### Sanity-checking the GitHub App on its own

```
ghrunner token test    # mints one registration token, prints it masked
```

## Config reference

| Field | Meaning |
|---|---|
| `github_app.app_id` | Your GitHub App's numeric ID. |
| `github_app.private_key_path` | The App's `.pem`, installed by `ghrunner init`. Must be mode `0600` or `ghrunner` refuses to use it. Never mounted into a container. |
| `repo` | `<owner>/<repo>` the fleet registers against. |
| `fleet.count` | Desired number of runners. |
| `fleet.name_prefix` | Runners are named `<prefix>-01`, `<prefix>-02`, ... |
| `fleet.labels` | Labels applied to every runner in the fleet. |
| `fleet.ephemeral` | `false` (default): register once, keep polling for jobs. `true`: deregister after every job. |
| `fleet.resources.{cpus,memory}` | Per-runner container limits. |
| `image.dockerfile_dir` / `image.tag` | Build context and tag for the runner image (see `docker/`). |

## Repo layout

```
ghrunner/
  cli.py           Typer commands (see `ghrunner --help`)
  config.py        ghrunner.yaml schema (Pydantic)
  github_app.py    JWT -> installation token -> registration token, list/deregister runners
  secrets.py       installs/reads the App's private key file (0600 enforced)
  compose.py       renders runner-template.yml + per-runner docker-compose.<name>.yml
  fleet.py         reconciles fleet.count against `docker ps`, shells `docker compose`
  watch.py         desync poll loop (GitHub API vs. local containers)
  init_config.py   `ghrunner init`'s questions + rendering them into ghrunner.yaml
  templates/       config.yaml.tmpl, systemd unit, launchd plist
docker/            runner image: Dockerfile + start.sh (registers/deregisters via config.sh)
tests/
```

## What this doesn't do (on purpose)

- No `HEALTHCHECK`/restart-policy logic in Python — that's Compose's job via
  `runner-template.yml`.
- No long-lived secrets (the GitHub App private key) ever reach a runner container — only the
  final, short-lived, single-use `REG_TOKEN` does, as an env var. That token is also in the
  runner's rendered compose file (`0600`, under `~/.local/state/ghrunner/compose/`) and in
  Docker's container config; once the runner has registered, it can't be reused.
- No deregistration from inside the container. Removing a registration takes a separate removal
  token, so `ghrunner down` deregisters from the host through the GitHub App, and `ghrunner
  watch` cleans up anything that gets missed.
- No cloud secret store. The Mac Mini is a single private host; fetching the key from S3 would
  only swap one on-disk secret (the `.pem`) for another (AWS credentials) and add a network
  dependency.
- No Apple `container` CLI backend. It was evaluated and dropped: none of this fleet's jobs invoke
  `docker`, so its per-VM isolation wasn't buying anything, and it has no restart-policy or
  `HEALTHCHECK` equivalent to build against. See `PLAN.md` §5 if that changes.
