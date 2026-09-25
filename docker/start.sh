#!/bin/bash
set -euo pipefail

: "${REPO:?REPO env var required}"
: "${REG_TOKEN:?REG_TOKEN env var required}"
: "${NAME:?NAME env var required}"

cd /home/runner/actions-runner

CONFIG_ARGS=(--url "https://github.com/${REPO}" --token "${REG_TOKEN}" --name "${NAME}" --unattended)

[ -n "${LABELS:-}" ]                  && CONFIG_ARGS+=(--labels "${LABELS}")
[ -n "${RUNNER_GROUP:-}" ]            && CONFIG_ARGS+=(--runnergroup "${RUNNER_GROUP}")
[ -n "${WORK_DIR:-}" ]                && CONFIG_ARGS+=(--work "${WORK_DIR}")
[ "${EPHEMERAL:-false}" = "true" ]    && CONFIG_ARGS+=(--ephemeral)
[ "${DISABLE_AUTO_UPDATE:-false}" = "true" ] && CONFIG_ARGS+=(--disableupdate)

# REG_TOKEN is single-use, so only configure on first start. After a Docker
# restart (restart: unless-stopped) the container keeps its filesystem and is
# already registered -- re-running config.sh would fail and crash-loop.
if [ ! -f .runner ]; then
  ./config.sh "${CONFIG_ARGS[@]}"
fi

# No `config.sh remove` on shutdown: that needs a removal token, not REG_TOKEN.
# `ghrunner down` deregisters from the host via the GitHub App instead, and
# `ghrunner watch` cleans up anything that slips through.
./run.sh &
RUNNER_PID=$!
trap 'kill -TERM "${RUNNER_PID}" 2>/dev/null' INT TERM
set +e
wait "${RUNNER_PID}"
rc=$?
# A trapped signal interrupts `wait` early; wait again for run.sh to finish.
if kill -0 "${RUNNER_PID}" 2>/dev/null; then
  wait "${RUNNER_PID}"
  rc=$?
fi
exit "${rc}"
