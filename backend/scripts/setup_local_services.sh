#!/usr/bin/env bash
# One-time local setup of the services OmniReview needs (Ubuntu or WSL with PostgreSQL 16 installed):
# PostgreSQL databases with pgvector, and Redis for rate limits and background jobs.
#
#   cd backend && ./scripts/setup_local_services.sh
#
# Run it as your normal user; it uses sudo only for the steps that need it. It installs pgvector and Redis,
# creates the "omnireview" role plus the omnireview (development) and omnireview_test (erased by every test run)
# databases, enables pgvector in both, starts Redis, and fills in DATABASE_URL, TEST_DATABASE_URL, and REDIS_URL
# in backend/.env. Safe to run again: existing roles, databases, and settings are kept.
set -euo pipefail

APP_ROLE=omnireview
DATABASES=(omnireview omnireview_test)
ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/.env"

psql_admin() {
  sudo -u postgres psql -v ON_ERROR_STOP=1 -qtA "$@"
}

echo "Installing pgvector and Redis..."
sudo apt-get install -y postgresql-16-pgvector redis-server >/dev/null

echo "Starting Redis..."
sudo service redis-server start >/dev/null

password=""
# Assigned first so a failed query (for example a wrong postgres password) stops the script instead of reading as
# "role missing". psql may ask for the postgres password if local connections require one.
role_exists="$(psql_admin -c "SELECT 1 FROM pg_roles WHERE rolname = '$APP_ROLE'")"
if [ "$role_exists" = "1" ]; then
  echo "Role $APP_ROLE already exists; keeping its password and your database settings."
else
  password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  psql_admin -c "CREATE ROLE $APP_ROLE LOGIN PASSWORD '$password'"
  echo "Created role $APP_ROLE."
fi

for database in "${DATABASES[@]}"; do
  database_exists="$(psql_admin -c "SELECT 1 FROM pg_database WHERE datname = '$database'")"
  if [ "$database_exists" != "1" ]; then
    sudo -u postgres createdb --owner "$APP_ROLE" "$database"
    echo "Created database $database."
  fi
  # Enabling pgvector needs a superuser; migrations then find it already enabled.
  psql_admin -d "$database" -c "CREATE EXTENSION IF NOT EXISTS vector"
done

python3 - "$ENV_FILE" "$password" <<'PYEOF'
import pathlib
import re
import sys

path, password = pathlib.Path(sys.argv[1]), sys.argv[2]
text = path.read_text() if path.exists() else ""
settings = {}
if password:
    settings["DATABASE_URL"] = f"postgresql+psycopg://omnireview:{password}@localhost:5432/omnireview"
    settings["TEST_DATABASE_URL"] = f"postgresql+psycopg://omnireview:{password}@localhost:5432/omnireview_test"
if not re.search(r"(?m)^REDIS_URL=\S", text):
    settings["REDIS_URL"] = "redis://localhost:6379/0"

for key, value in settings.items():
    line = f"{key}={value}"
    text, replaced = re.subn(rf"(?m)^{key}=.*$", lambda _: line, text)
    if not replaced:
        text = text.rstrip("\n") + f"\n{line}\n"
path.write_text(text)
if settings:
    print(f"Updated {', '.join(settings)} in {path}")
PYEOF

echo "Services are ready. Next: uv run alembic upgrade head && uv run python -m scripts.seed_dev"
