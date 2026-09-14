"""Alembic environment: uses the app's engine and models so migrations and code share one definition."""

import sys
from pathlib import Path

from alembic import context
from sqlalchemy import Connection

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import models  # noqa: E402, F401  (registers every table on Base.metadata)
from database import Base, engine  # noqa: E402

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=engine.url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    sqlite = connection.dialect.name == "sqlite"
    if sqlite:
        # Batch mode rebuilds a SQLite table by copying it and dropping the original, which foreign key enforcement
        # blocks once other tables reference its rows. Pause enforcement and verify integrity afterwards.
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
    try:
        # Batch mode lets ALTER TABLE migrations work on SQLite as well as PostgreSQL.
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
        if sqlite:
            violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(f"Migrations left foreign key violations: {violations[:5]}")
    finally:
        if sqlite:
            connection.rollback()
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()


def run_migrations_online() -> None:
    # Tests can pass their own connection to migrate a database other than the app's.
    provided = context.config.attributes.get("connection")
    if provided is not None:
        _run_migrations(provided)
        return
    with engine.connect() as connection:
        _run_migrations(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
