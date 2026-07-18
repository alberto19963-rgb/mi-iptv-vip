from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_env_settings


class Base(DeclarativeBase):
    pass


_settings = get_env_settings()
engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    pool_size=3,
    max_overflow=2,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_columns() -> None:
    """Add columns introduced after initial create_all (Postgres only)."""
    from sqlalchemy import text

    alters = [
        "ALTER TABLE download_requests ADD COLUMN IF NOT EXISTS estimated_size_gb DOUBLE PRECISION",
        "ALTER TABLE download_requests ADD COLUMN IF NOT EXISTS quality_profile_id INTEGER",
        "ALTER TABLE download_requests ADD COLUMN IF NOT EXISTS resolution_cap VARCHAR(16)",
    ]
    with engine.begin() as conn:
        for stmt in alters:
            try:
                conn.execute(text(stmt))
            except Exception:
                # Non-Postgres or table not ready yet — create_all will handle base schema
                pass


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_columns()
