import os
from collections.abc import Iterator

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

load_dotenv()


def _database_url() -> str:
    """PostgreSQL (with pgvector) is required in every environment; there is no SQLite fallback."""
    raw_url = os.getenv("DATABASE_URL", "").strip()
    if not raw_url:
        raise RuntimeError(
            "DATABASE_URL is not set. OmniReview needs PostgreSQL with pgvector: run "
            "backend/scripts/setup_local_services.sh, or see Local development in README.md."
        )
    url = make_url(raw_url)
    if url.get_backend_name() != "postgresql":
        raise RuntimeError(f"DATABASE_URL must point to PostgreSQL, not {url.get_backend_name()}.")
    # Always use the psycopg 3 driver, whichever driver the URL names.
    return url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


SQLALCHEMY_DATABASE_URL = _database_url()

engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
