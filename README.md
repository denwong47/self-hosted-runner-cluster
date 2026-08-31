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

- **You never touch a registration token.** `ghrunner` holds a GitHub App private key (fetched
  from S3) and AWS creds (host-side only, never inside a container). On every `up`/`watch` action
  it exchanges those for a fresh, single-use runner registration token per runner
  ([`github_app.py`](ghrunner/github_app.py)).
- **Docker Compose owns runtime lifecycle.** `ghrunner` doesn't run its own supervisor loop for
  crash-restart or health polling — it renders a shared `runner-template.yml` (with
  `restart: unless-stopped` and a `HEALTHCHECK`) plus one thin `docker-compose.<name>.yml` per
  runner that `extends` it ([`compose.py`](ghrunner/compose.py)), then shells out to
  `docker compose up -d`. Docker does the restarting.
- **`ghrunner` only tracks one thing Compose can't see: registration desync.** A runner container
  can be up and healthy locally while GitHub's side thinks it's offline or has removed it (or vice
  versa) — e.g. an admin removed it from GitHub's UI, or a container died in a way its
  deregister trap didn't catch. `ghrunner watch` polls the GitHub API and the local container list
  and heals the difference ([`watch.py`](ghrunner/watch.py)).
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
2. **Upload its private key (`.pem`) to S3**, and note the bucket/key.
3. *(Optional)* Put the App's client secret in AWS SSM as a `SecureString` if you plan to use it
   later — it isn't required for the registration-token flow itself.
4. **Initialize the config:**

   ```
   ghrunner init
   ```

   This writes `~/.config/ghrunner/ghrunner.yaml` from the sample template and refuses to
   overwrite an existing file. Edit it:

   ```yaml
   github_app:
     app_id: "123456"
     private_key_s3:
       bucket: "my-org-secrets"
       key: "github-app/ghrunner.pem"
     aws_region: "us-east-1"

   repo: "my-org/my-repo"

   fleet:
     count: 5
     name_prefix: "runner"
     labels: [self-hosted, linux, arm64]

   image:
     dockerfile_dir: "docker"
     tag: "ghrunner/runner:latest"
   ```

5. **Validate it end-to-end** (schema + AWS creds + a live GitHub App auth round-trip that mints
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
ghrunner down --name runner-03   # stop + deregister just one runner
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
| `github_app.private_key_s3.{bucket,key}` | Where the App's `.pem` lives in S3. Fetched into memory only, never written to disk or into a container. |
| `github_app.aws_region` | Region for the S3/SSM calls. |
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
  secrets.py       host-side S3/SSM fetches for the above
  compose.py       renders runner-template.yml + per-runner docker-compose.<name>.yml
  fleet.py         reconciles fleet.count against `docker ps`, shells `docker compose`
  watch.py         desync poll loop (GitHub API vs. local containers)
  templates/       config.sample.yaml, systemd unit, launchd plist
docker/            runner image: Dockerfile + start.sh (registers/deregisters via config.sh)
tests/
```

## What this doesn't do (on purpose)

- No `HEALTHCHECK`/restart-policy logic in Python — that's Compose's job via
  `runner-template.yml`.
- No secrets (AWS creds, GitHub App private key) ever reach a runner container — only the final,
  short-lived `REG_TOKEN` does, as an env var.
- No Apple `container` CLI backend. It was evaluated and dropped: none of this fleet's jobs invoke
  `docker`, so its per-VM isolation wasn't buying anything, and it has no restart-policy or
  `HEALTHCHECK` equivalent to build against. See `PLAN.md` §5 if that changes.
