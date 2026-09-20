#!/usr/bin/env bash
# Deploy a version to the production server, with a backup first, a health gate, and a code rollback on failure.
#
#   ops/deploy.sh v1.2.0        # a tag, branch, or commit
#
# GitHub Actions runs exactly this over SSH for every push to main that passes the checks (.github/workflows/ci.yml).
#
# Database migrations run when the API starts. They can't be undone by switching code back, so if a deploy fails after
# migrating, this script rolls the code back and tells you how to restore the pre-deploy backup.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
VERSION="${1:?Give the tag, branch, or commit to deploy}"
# The deploy settings live in .env beside the compose file (SITE_ADDRESS, POSTGRES_PASSWORD, BACKUP_DIR). Docker
# Compose reads that file by itself; this reads it too, so a deploy over SSH needs nothing in its environment.
if [ -r "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091  # written on the server, never committed
  . "$ROOT/.env"
  set +a
fi
COMPOSE=(docker compose -f "$ROOT/docker-compose.prod.yml")
SITE="${SITE_ADDRESS:?Set SITE_ADDRESS in .env, e.g. omnireview.duckdns.org}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-300}"
cd "$ROOT"

PREVIOUS="$(git rev-parse HEAD)"
echo "Deploying $VERSION (currently $PREVIOUS)"
git fetch --tags origin
"$HERE/backup.sh"
BACKUP="$(find "${BACKUP_DIR:-/var/backups/omnireview}" -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"

wait_healthy() {
  local deadline=$((SECONDS + HEALTH_TIMEOUT))
  until curl -fsS "https://$SITE/healthz" > /dev/null && "${COMPOSE[@]}" exec -T api python -c \
      "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=5)"; do
    if [ "$SECONDS" -ge "$deadline" ]; then return 1; fi
    sleep 5
  done
}

git checkout --detach "$VERSION"
if "${COMPOSE[@]}" build && "${COMPOSE[@]}" up -d && wait_healthy \
    && "${COMPOSE[@]}" exec -T api python -m scripts.production_check --strict; then
  echo "Deployed $VERSION. Now run the smoke test with a real account:"
  echo "  ${COMPOSE[*]} exec api python -m scripts.smoke_test --base-url http://127.0.0.1:8000 --email ... --password ..."
  exit 0
fi

echo "Deploy of $VERSION failed; rolling the code back to $PREVIOUS" >&2
git checkout --detach "$PREVIOUS"
"${COMPOSE[@]}" build && "${COMPOSE[@]}" up -d
if wait_healthy; then
  echo "Rolled back to $PREVIOUS and healthy." >&2
else
  echo "The rollback isn't healthy either." >&2
fi
echo "If the failed version migrated the database, restore the pre-deploy backup: ops/restore.sh $BACKUP" >&2
exit 1
