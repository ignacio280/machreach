#!/usr/bin/env bash
# Nightly pg_dump of the production database, kept for BACKUP_KEEP_DAYS.
# Custom format, restorable with pg_restore (or scripts/restore_drill.ps1).
# Copy the newest file somewhere off the server now and then; a backup on the
# same disk as the database is only a backup against mistakes, not disasters.
set -euo pipefail

BACKUP_DIR=${BACKUP_DIR:-/var/backups/machreach}
KEEP_DAYS=${BACKUP_KEEP_DAYS:-14}
cd "${MACHREACH_DIR:-/opt/machreach}/deploy"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
file="$BACKUP_DIR/machreach-$(date -u +%Y%m%d-%H%M%S).dump"
docker compose exec -T db pg_dump -U machreach --format custom --no-owner --no-privileges machreach > "$file"
chmod 600 "$file"
find "$BACKUP_DIR" -name 'machreach-*.dump' -mtime +"$KEEP_DAYS" -delete
echo "[backup] wrote $file ($(du -h "$file" | cut -f1)), keeping $KEEP_DAYS days"
