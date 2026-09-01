#!/usr/bin/env bash
# Deploy the current origin/master when its CI checks have passed.
#
# Runs from the machreach-deploy systemd timer every two minutes, and from
# the bootstrap. Idempotent: nothing happens while master has not moved or
# while its checks are still running. `--force` deploys HEAD as it is
# (bootstrap, or an operator rolling forward by hand).
#
# Order matters and mirrors Render: build the image, run migrations against
# the live database with the new code, then swap the containers. The old
# containers keep serving until `up` replaces them.
set -euo pipefail

REPO_DIR=${MACHREACH_DIR:-/opt/machreach}
REPO_SLUG=${MACHREACH_REPO:-ignacio280/machreach}
cd "$REPO_DIR"
# The branch the server was cloned from, until the checkout is switched.
BRANCH=${MACHREACH_BRANCH:-$(git rev-parse --abbrev-ref HEAD)}

log() { echo "[deploy $(date -u +%H:%M:%S)] $*"; }

if [[ "${1:-}" != "--force" ]]; then
    git fetch -q origin "$BRANCH"
    target=$(git rev-parse "origin/$BRANCH")
    current=$(git rev-parse HEAD)
    if [[ "$target" == "$current" ]]; then
        exit 0
    fi
    if ! python3 deploy/checks_passed.py "$REPO_SLUG" "$target"; then
        log "not deploying ${target:0:12} yet"
        exit 0
    fi
    log "deploying ${current:0:12} -> ${target:0:12}"
    git reset -q --hard "$target"
fi

export GIT_COMMIT
GIT_COMMIT=$(git rev-parse HEAD)
cd deploy

docker compose build --pull web
docker compose up -d db
docker compose run --rm --no-deps web python migrate.py
docker compose up -d --remove-orphans
docker image prune -f >/dev/null
log "deployed ${GIT_COMMIT:0:12}"
