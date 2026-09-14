"""Migrations must apply to databases that already hold data, not only to the empty ones the other tests use."""

import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def migrate(connection, revision):
    config = Config(str(ALEMBIC_INI))
    config.attributes["connection"] = connection
    command.upgrade(config, revision)


def test_upgrading_a_sqlite_database_that_has_users_projects_and_members():
    engine = create_engine(f"sqlite:///{Path(tempfile.mkdtemp()) / 'existing.db'}")
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        migrate(connection, "0002")
        connection.execute(
            text(
                "INSERT INTO users (id, first_name, last_name, email, hashed_password, is_active, created_at) "
                "VALUES (1, 'Ada', 'Lovelace', 'ada@example.org', 'not-a-hash', 1, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO projects (id, title, owner_id, created_at) "
                "VALUES (1, 'Existing review', 1, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO project_members (project_id, user_id, role, created_at) "
                "VALUES (1, 1, 'owner', CURRENT_TIMESTAMP)"
            )
        )
        connection.commit()

        migrate(connection, "head")

        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0003"
        assert connection.execute(text("SELECT count(*) FROM protocols WHERE project_id = 1")).scalar() == 1
        assert connection.execute(text("SELECT count(*) FROM extraction_fields WHERE project_id = 1")).scalar() == 4
        assert connection.execute(text("SELECT count(*) FROM project_members")).scalar() == 1
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
