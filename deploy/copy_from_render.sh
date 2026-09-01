#!/usr/bin/env bash
# Copy the Render database into this server's Postgres and verify it.
#
#   copy_from_render.sh            first fill of an empty database (bootstrap)
#   copy_from_render.sh --replace  cutover: stop the app, wipe, copy, verify, start
#
# Reads RENDER_DATABASE_URL from deploy/.env. The dump file is kept under
# /var/backups/machreach as the last full copy of the Render database.
set -euo pipefail

cd "${MACHREACH_DIR:-/opt/machreach}/deploy"
mode=${1:-}
BACKUP_DIR=${BACKUP_DIR:-/var/backups/machreach}
mkdir -p "$BACKUP_DIR" && chmod 700 "$BACKUP_DIR"

source_url=$(grep -E '^RENDER_DATABASE_URL=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
if [[ -z "$source_url" ]]; then
    echo "[copy] RENDER_DATABASE_URL is empty in deploy/.env; nothing to copy" >&2
    exit 1
fi

docker compose up -d db
if [[ "$mode" == "--replace" ]]; then
    echo "[copy] stopping web and worker so nothing writes during the copy"
    docker compose stop web worker
    docker compose exec -T db psql -U machreach -d machreach -v ON_ERROR_STOP=1 \
        -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
fi

dump="$BACKUP_DIR/render-$(date -u +%Y%m%d-%H%M%S).dump"
echo "[copy] dumping Render database to $dump"
docker compose run --rm --no-deps --entrypoint pg_dump db \
    --format custom --no-owner --no-privileges "$source_url" > "$dump"
chmod 600 "$dump"

echo "[copy] restoring"
docker compose exec -T db pg_restore -U machreach -d machreach \
    --no-owner --no-privileges --exit-on-error < "$dump"

echo "[copy] running migrations"
docker compose run --rm --no-deps web python migrate.py

echo "[copy] verifying row counts"
docker compose run --rm --no-deps -e SOURCE_DATABASE_URL="$source_url" web \
    sh -c 'python scripts/copy_database.py --verify-only --source "$SOURCE_DATABASE_URL" --target "$DATABASE_URL"'

if [[ "$mode" == "--replace" ]]; then
    docker compose up -d
    echo "[copy] web and worker started"
fi
echo "[copy] done"
