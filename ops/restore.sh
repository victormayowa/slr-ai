#!/usr/bin/env bash
# Restore a backup made by ops/backup.sh into the production database and document storage. DESTRUCTIVE: the current
# database and documents are replaced.
#
#   ops/restore.sh /var/backups/omnireview/20260916T031500Z
#
# Stop the API and worker first so nothing writes during the restore:
#   docker compose -f docker-compose.prod.yml stop api worker caddy
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
SOURCE="${1:?Give the backup folder to restore}"
COMPOSE=(docker compose -f "$ROOT/${COMPOSE_FILE:-docker-compose.prod.yml}")

( cd "$SOURCE" && sha256sum --check SHA256SUMS )
read -r -p "This replaces the production database and documents with $SOURCE. Type 'restore' to continue: " answer
[ "$answer" = "restore" ] || { echo "Cancelled"; exit 1; }

"${COMPOSE[@]}" up -d postgres
"${COMPOSE[@]}" exec -T postgres pg_restore -U omnireview -d omnireview --clean --if-exists --no-owner < "$SOURCE/database.dump"
"${COMPOSE[@]}" run --rm --no-deps -T -v "$SOURCE:/restore:ro" api sh -c \
  'rm -rf /data/documents/* && tar -C /data -xzf /restore/documents.tar.gz'
"${COMPOSE[@]}" up -d
echo "Restored. Check the site, then run: ${COMPOSE[*]} exec api python -m scripts.production_check"
