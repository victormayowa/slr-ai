#!/usr/bin/env bash
# Prove the latest backup can be restored, without touching production: restore it into a scratch database, check the
# schema version and row counts, then drop it. Run monthly (ops/systemd/omnireview-restore-drill.timer).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
[ -f "$HERE/backup.env" ] && set -a && . "$HERE/backup.env" && set +a
BACKUP_DIR="${BACKUP_DIR:-/var/backups/omnireview}"
COMPOSE=(docker compose -f "$ROOT/${COMPOSE_FILE:-docker-compose.prod.yml}")
LATEST="$(find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"
[ -n "$LATEST" ] || { echo "No backups in $BACKUP_DIR" >&2; exit 1; }
( cd "$LATEST" && sha256sum --check SHA256SUMS )

DRILL=omnireview_restore_drill
psql_run() { "${COMPOSE[@]}" exec -T postgres psql -U omnireview -v ON_ERROR_STOP=1 "$@"; }
psql_run -d postgres -c "DROP DATABASE IF EXISTS $DRILL" -c "CREATE DATABASE $DRILL"
trap 'psql_run -d postgres -c "DROP DATABASE IF EXISTS $DRILL" >/dev/null' EXIT
psql_run -d "$DRILL" -c "CREATE EXTENSION IF NOT EXISTS vector" >/dev/null
"${COMPOSE[@]}" exec -T postgres pg_restore -U omnireview -d "$DRILL" --no-owner < "$LATEST/database.dump"

VERSION="$(psql_run -d "$DRILL" -tAc "SELECT version_num FROM alembic_version")"
USERS="$(psql_run -d "$DRILL" -tAc "SELECT count(*) FROM users")"
PROJECTS="$(psql_run -d "$DRILL" -tAc "SELECT count(*) FROM projects")"
tar -tzf "$LATEST/documents.tar.gz" > /dev/null
echo "Restored $LATEST: schema $VERSION, $USERS users, $PROJECTS projects, documents archive readable"

"${COMPOSE[@]}" exec -T api python -m scripts.record_heartbeat restore_drill \
  --detail "backup=$LATEST" --detail "schema=$VERSION" --detail "users=$USERS" --detail "projects=$PROJECTS"
