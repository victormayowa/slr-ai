import os
from collections.abc import Iterator

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

load_dotenv()

# SQLite for local development; PostgreSQL (via DATABASE_URL) in docker compose and production.
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./omnireview.db")

connect_args: dict[str, object] = {}
if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)

if engine.dialect.name == "sqlite":

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
        # SQLite ignores foreign keys (and ON DELETE CASCADE) unless enabled per connection.
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
