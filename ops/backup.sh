#!/usr/bin/env bash
# Nightly backup of the production database and document files, with retention and optional off-site copy.
#
#   ops/backup.sh                    # from the repository root on the server
#
# Settings (environment, or ops/backup.env next to this script):
#   BACKUP_DIR        where backups are written (default /var/backups/omnireview)
#   BACKUP_KEEP_DAYS  local copies to keep (default 14)
#   RESTIC_REPOSITORY and RESTIC_PASSWORD_FILE (plus the storage credentials restic needs) to copy each backup
#                     off-site, encrypted. Strongly recommended: a backup on the same server isn't a backup.
#   COMPOSE_FILE      default docker-compose.prod.yml
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
[ -f "$HERE/backup.env" ] && set -a && . "$HERE/backup.env" && set +a

BACKUP_DIR="${BACKUP_DIR:-/var/backups/omnireview}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
COMPOSE=(docker compose -f "$ROOT/${COMPOSE_FILE:-docker-compose.prod.yml}")
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TARGET="$BACKUP_DIR/$STAMP"

mkdir -p "$TARGET"
chmod 700 "$BACKUP_DIR" "$TARGET"
echo "Backing up to $TARGET"

# The database, in PostgreSQL's custom format (compressed; restores table by table).
"${COMPOSE[@]}" exec -T postgres pg_dump -U omnireview -d omnireview --format=custom --no-owner > "$TARGET/database.dump"
# Document files (full texts, plots, uploads) from the API's storage volume.
"${COMPOSE[@]}" exec -T api tar -C /data -czf - documents > "$TARGET/documents.tar.gz"

for file in database.dump documents.tar.gz; do
  if [ ! -s "$TARGET/$file" ]; then
    echo "Backup failed: $file is empty" >&2
    exit 1
  fi
done
( cd "$TARGET" && sha256sum database.dump documents.tar.gz > SHA256SUMS )
SIZE="$(du -sb "$TARGET" | cut -f1)"

if [ -n "${RESTIC_REPOSITORY:-}" ]; then
  restic backup --tag omnireview "$TARGET"
  restic forget --tag omnireview --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --prune
fi

find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -mtime +"$KEEP_DAYS" -exec rm -rf {} +

"${COMPOSE[@]}" exec -T api python -m scripts.record_heartbeat backup --detail "size_bytes=$SIZE" --detail "path=$TARGET"
echo "Backup complete ($SIZE bytes)"
