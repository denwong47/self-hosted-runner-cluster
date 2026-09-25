# `ghrunner` — CLI for a self-hosted GitHub Actions runner fleet

> [!NOTE]
> This repo, `self-hosted-runner-cluster`, replaces an existing repo `self-hosted-runner`, which is
> forked and have a different purpose. Whenever this document refers to replacing existing
> functionality, it means replacing what was previously done by `self-hosted-runner`.

Replaces the current single-instance `docker compose -f docker/docker-compose.yml up` workflow
with a Python CLI that: (1) manages a fleet of N runner containers under one label instead of one,
(2) targets real Docker + Compose (see §5 for why Apple's `container` CLI was
dropped as the near-term backend), and (3) fetches GitHub registration tokens programmatically via
a GitHub App instead of a manually-copied `REG_TOKEN`.

Scope decisions already made:
- Registration tokens are **repo-scoped** (`REPO=<owner>/<repo>`), matching the existing `.env.example`.
- Fleet size is expressed as **count + name prefix** (e.g. `count: 5`, `name_prefix: runner` →
  `runner-01 .. runner-05`), all sharing one label set.
- Runners are **persistent** (`ephemeral: false`) — register once, keep polling. GitHub's runner
  binary already retries/reconnects through short network blips on its own, so `ephemeral: false`
  doesn't need any extra help for that case. The real risk is *registration desync* (GitHub's view
  of a runner's state diverging from what's actually running locally) — see §5, which replaces the
  original "is my internet drop a problem?" open question.
- **Docker + Compose is the backend**, not Apple's `container` CLI. None of the CI jobs on this
  fleet invoke `docker` themselves, so the Docker-socket/DinD risk that motivated evaluating Apple
  Container doesn't apply. Compose's native `restart:` and `HEALTHCHECK` are preferred over
  reimplementing that logic in Python — see §5.
- Registration tokens are minted **host-side only**. `github_app.py` and `secrets.py` (the PEM)
  never run inside a runner container; the container only ever receives the final,
  short-lived `REG_TOKEN` as an env var, matching the existing `start.sh` contract. This keeps the
  GitHub App private key off of every container's attack surface.
- **No cloud provider.** The private key is a local `0600` file installed by `ghrunner init`. The
  host is a single private Mac Mini, so an S3/SSM fetch would just swap one on-disk secret (the
  PEM) for another (AWS creds) while adding a network dependency and an AWS account to manage.

## 1. Package layout

```
ghrunner/
  pyproject.toml                    # entry point: ghrunner = ghrunner.cli:app
  ghrunner/
    cli.py                          # Typer app, subcommands
    config.py                       # Pydantic models + YAML loader/validator
    github_app.py                  # JWT signing, installation token, registration-token fetch
    secrets.py                      # install/read the local PEM file, 0600 enforced
    compose.py                      # renders runner-NN.yml from runner-template.yml via `extends`
    fleet.py                        # reconciles desired count -> actual compose services
    watch.py                        # GitHub registration desync poll loop (see §5)
    templates/
      config.yaml.tmpl
  docker/                            # existing Dockerfile + start.sh, reused as-is
tests/
```

Stack: **Typer** (CLI), **Pydantic** (config validation), **PyJWT** +
**cryptography** (GitHub App JWT signing), **PyYAML** (compose file rendering). Installable with
`pipx install .` / `uv tool install .`.

## 2. Config file

`ghrunner.yaml`, written by `ghrunner init`, which asks for every value below (each also
available as a flag, e.g. `--repo`, for scripted setup) and re-asks on invalid input. Once all
answers are in, it copies the downloaded GitHub App `.pem` next to the config as `github-app.pem`
with mode `0600` and renders `templates/config.yaml.tmpl` to the real path (default
`~/.config/ghrunner/ghrunner.yaml`). It refuses to overwrite an existing config.
`image.dockerfile_dir` is stored absolute so the service-managed `watch` doesn't depend on its
working directory.

```yaml
github_app:
  app_id: "123456"
  private_key_path: "~/.config/ghrunner/github-app.pem"   # installed by `ghrunner init`, 0600

repo: "my-org/my-repo"

fleet:
  count: 5
  name_prefix: "runner"
  labels: [self-hosted, linux, arm64]
  work_dir: "_work"
  ephemeral: false
  disable_auto_update: false
  resources:
    cpus: 4
    memory: "6G"

backend: docker                         # docker only for now — see §5

image:
  dockerfile_dir: "docker"
  tag: "ghrunner/runner:latest"
```

`config.py` validates this with Pydantic (required fields, enum for `backend`, cross-field checks
like `count >= 1`), and exposes a `Config.load(path)` used by every CLI command.

## 3. GitHub App token flow (`github_app.py`)

Per the discussion in github/community#27204, replacing manual `REG_TOKEN` copy-paste. This entire
chain runs **on the host** (never inside a container):

1. `secrets.py` reads the PEM from `github_app.private_key_path`, refusing it if the file is
   group/world-accessible (anything looser than `0600`).
2. `github_app.py` builds a JWT (`iss=app_id`, `iat`/`exp` ~9 min window) signed with that key.
3. `GET /app/installations` (Bearer: JWT) → find the installation for the target repo/org.
4. `POST /app/installations/{id}/access_tokens` → short-lived **installation access token**.
5. `POST /repos/{owner}/{repo}/actions/runners/registration-token` (Bearer: installation token) →
   the actual runner registration token (1-hour TTL, single-use for `config.sh`).

Each `ghrunner up` invocation mints one registration token per runner it needs to (re)register, and
passes it into that runner's compose file as `REG_TOKEN` (§4). The GitHub App private key is only
ever touched by the host process — no secrets material is mounted into or installed inside runner
containers.

Only `app_id` + the private key are needed; the App's OAuth `client_id`/`client_secret` play no part
in this flow and aren't in the config.

## 4. Compose rendering (`compose.py`) — Docker owns restart/health

Rather than a `ContainerBackend` protocol shelling out per-container `docker run` commands,
`ghrunner` renders thin per-runner Compose files that `extends` a shared template, and lets
`docker compose up -d` own the runtime lifecycle:

`runner-template.yml` (rendered by `compose.py` from the config's `image`/`fleet.resources` fields
into the compose work dir alongside the per-runner files, not shipped as a static file):

```yaml
services:
  runner:
    build: ../docker
    image: ghrunner/runner:latest
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "pgrep", "-f", "run.sh"]
      interval: 30s
      timeout: 5s
      retries: 3
    mem_limit: 6g
    cpus: 4
```

`compose.py` renders `docker-compose.runner-03.yml` (one per fleet member) into a
per-runner working dir:

```yaml
services:
  runner:
    extends:
      file: ../runner-template.yml
      service: runner
    container_name: runner-03
    environment:
      NAME: runner-03
      REPO: my-org/my-repo
      LABELS: self-hosted,linux,arm64
      REG_TOKEN: <minted>              # written at `up` time; file is 0600, token single-use
      EPHEMERAL: "false"
```

`fleet.py` calls `docker compose -f <rendered file> up -d` per runner (or `--force-recreate` when
healing a desync, see §5). Restart policy and health polling are entirely Compose/Docker's job now
— `watch.py` no longer needs to reimplement either.

Apple's `container` CLI is **not** being built against right now (see §5) — if it's revisited
later, the `ContainerBackend` protocol from the original plan is the right shape to reintroduce,
but it's not worth maintaining until Compose-equivalent restart/health/DinD support exists there.

## 5. Why Apple Container is deferred, and what replaces its `watch.py` role

Apple's `container` CLI was evaluated as the primary backend, but two things changed the call:

- No CI jobs on this fleet invoke `docker`, so the original DinD/socket-mount concern that made
  `container`'s per-VM isolation interesting doesn't actually apply here.
- `container` has no declarative restart policy and no `HEALTHCHECK` equivalent — using it would
  mean re-implementing both in `watch.py`, which is real ongoing maintenance for something Compose
  already does correctly today. Given the jobs don't need `container`'s isolation model, that
  tradeoff isn't worth it right now.

So the backend is Docker + Compose (§4), and `watch.py`'s scope shrinks to the one thing Compose
*can't* see: whether GitHub's registration state has silently diverged from what's running
locally. That can happen if, e.g., a runner is removed from GitHub's side (admin action, org
policy, idle cleanup) while its container is still up and polling, or a container goes away
without `ghrunner down` deregistering it.

`watch.py` only considers runners named `<name_prefix>-NN`. Anything else registered to the repo
(other hosts, other prefixes) is never recreated or deregistered.

`watch.py` poll loop (default 60s):

1. `GET /repos/{owner}/{repo}/actions/runners` (via `github_app.py`, host-side) → GitHub's view:
   name, `status` (`online`/`offline`), `busy`.
2. `docker compose ps` (or `docker ps --filter name=<prefix>`) → local view: which runner
   containers are actually running.
3. Diff the two lists:
   - Local running + GitHub `offline` for **N consecutive polls** (default 3, i.e. ~3 min) →
     desync. Heal by minting a fresh registration token and
     `docker compose up -d --force-recreate` for that one runner.
   - GitHub has a name with no matching local container → stale registration; deregister it via
     the GitHub API directly (no container to restart).
   - Local container running + no matching GitHub entry at all → treat like the first case
     (force-recreate with a fresh token).
4. Log every heal action (name, reason, timestamp) — this is the audit trail for "was my fleet
   ever actually down."

This is a light poll loop, not a health/restart supervisor — Docker already restarts crashed
containers per `restart: unless-stopped`, and `HEALTHCHECK` already marks them unhealthy for
`docker ps` / alerting. `watch.py` only exists to catch the GitHub-side view drifting from reality.

## 6. Fleet reconciliation (`fleet.py`)

- Desired names = `[f"{prefix}-{i:02d}" for i in range(1, count+1)]`.
- Diff against `docker compose ps` (or `docker ps --filter name=<prefix>`) for the name prefix.
- For each missing name: mint a fresh registration token (§3), render its compose file (§4),
  `docker compose up -d`.
- For extras beyond `count`: `docker compose down`, then deregister from GitHub host-side via the
  App (`DELETE .../actions/runners/{id}`). `start.sh` can't do this itself: `config.sh remove` needs
  a removal token, not the registration token it was started with. If the delete fails (e.g. the
  runner is still marked busy), `watch` removes the stale registration on a later poll.
- Run `ghrunner watch` under whatever service manager the host already uses (systemd unit /
  launchd plist / cron @reboot — a template for each ships in `templates/`) so it survives
  reboots. This is the one piece of "supervision" `ghrunner` still owns, since it's
  GitHub-registration-specific and Compose has no visibility into it.

## 7. CLI surface

```
ghrunner init [--<setting> ...]    # ask for every setting, install the App .pem (0600), write config
ghrunner config validate           # schema check + private key check + GitHub App auth dry-run
ghrunner up [--count N]            # build image if needed, render compose files, reconcile fleet
ghrunner down [--all | --name X]   # docker compose down one or all runners, deregister
ghrunner status                    # local docker compose ps + GitHub registration state, side by side
ghrunner logs <name> [--follow]    # docker compose logs
ghrunner scale <N>                 # shorthand for `up --count N`
ghrunner watch                     # foreground/service-manager desync poll loop (§5)
ghrunner token test                # mint one registration token, print masked, confirm the App works
```

(`ghrunner backend probe` from the earlier draft is dropped along with the Apple Container spike —
nothing left to probe until that backend is revisited.)

## 8. Rollout phases

1. **Docker + Compose backend + config + GitHub App token flow.** `compose.py` template/render,
   `fleet.py` reconciliation, host-side token minting into `REG_TOKEN`. This alone replaces today's
   manual `.env` + single-instance compose workflow with a fleet of N.
2. **`watch.py` desync poll loop + service-manager persistence.** GitHub-runners-API vs.
   local-compose-ps diff and heal, per §5.
3. **Packaging.** `pyproject.toml`, pipx-installable, systemd unit + launchd plist templates, README rewrite.

Apple Container is not on this roadmap for now; if job requirements change (e.g. a future job
needs `container`'s stronger per-VM isolation), revisit §5 and reintroduce a `ContainerBackend`
abstraction rather than retrofitting Compose-shaped assumptions onto it.
