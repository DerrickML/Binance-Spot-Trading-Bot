"""Database engine and session management."""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import get_logger
from app.persistence.models import Base

logger = get_logger(__name__)

_engines: dict[str, Engine] = {}
_SessionFactories: dict[str, sessionmaker[Session]] = {}


def get_engine(database_url: str | None = None):
    """Get or create the SQLAlchemy engine.

    Args:
        database_url: Database connection URL. Defaults to DATABASE_URL env var or SQLite.
    """
    url = database_url or os.getenv("DATABASE_URL", "sqlite:///data/trading_bot.db")
    
    if url not in _engines:
        # Ensure SQLite directory exists
        if url.startswith("sqlite:///"):
            db_path = url.replace("sqlite:///", "")
            db_dir = os.path.dirname(db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)

        connect_args = {}
        if url.startswith("sqlite"):
            connect_args["check_same_thread"] = False

        _engines[url] = create_engine(
            url,
            echo=False,
            connect_args=connect_args,
            pool_pre_ping=True,
        )
        logger.info("database_engine_created", url=url.split("@")[-1])
    return _engines[url]


def get_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    """Get or create the session factory."""
    url = database_url or os.getenv("DATABASE_URL", "sqlite:///data/trading_bot.db")
    if url not in _SessionFactories:
        engine = get_engine(url)
        _SessionFactories[url] = sessionmaker(bind=engine, expire_on_commit=False)
    return _SessionFactories[url]


def get_session(database_url: str | None = None) -> Session:
    """Create a new database session.

    For isolated, per-URL sessions (e.g. audit replay against sqlite:///:memory:)
    use make_session() directly to avoid cross-contaminating the global singleton.
    """
    factory = get_session_factory(database_url)
    return factory()


def make_session(database_url: str) -> Session:
    """Create a one-off isolated session for a specific database URL.

    Use this when you need a connection to a different DB than the main app
    (e.g. audit replay against an in-memory SQLite) without disturbing the
    global engine cache.
    """
    connect_args: dict[str, Any] = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if database_url.startswith("sqlite:///") and database_url != "sqlite:///:memory:":
            db_path = database_url.replace("sqlite:///", "")
            db_dir = os.path.dirname(db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)

    engine = create_engine(
        database_url,
        echo=False,
        connect_args=connect_args,
        pool_pre_ping=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return factory()


def init_db(database_url: str | None = None) -> None:
    """Initialize the database — create all tables and run migrations."""
    engine = get_engine(database_url)
    Base.metadata.create_all(engine)
    _migrate_selected_strategies(engine)
    logger.info("database_initialized", tables=list(Base.metadata.tables.keys()))


def _migrate_selected_strategies(engine) -> None:
    """Add missing columns to selected_strategies if it predates Phase 4.

    This is a lightweight migration until Alembic is integrated.
    Each ALTER TABLE is wrapped in try/except so it's safe to run repeatedly.
    """
    from sqlalchemy import text, inspect as sa_inspect

    inspector = sa_inspect(engine)
    if "selected_strategies" not in inspector.get_table_names():
        return

    existing = {col["name"] for col in inspector.get_columns("selected_strategies")}
    new_columns = {
        "qualified": "BOOLEAN DEFAULT 0",
        "qualification_failures": "TEXT DEFAULT '[]'",
        "benchmark_return_pct": "FLOAT DEFAULT 0.0",
        "oos_consistency": "FLOAT DEFAULT 0.0",
        "degradation_ratio": "FLOAT DEFAULT 0.0",
        "validation_windows": "INTEGER DEFAULT 0",
        "validation_context": "TEXT DEFAULT '{}'",
    }

    with engine.connect() as conn:
        for col_name, col_def in new_columns.items():
            if col_name not in existing:
                try:
                    conn.execute(text(
                        f"ALTER TABLE selected_strategies ADD COLUMN {col_name} {col_def}"
                    ))
                    conn.commit()
                    logger.info("migration_column_added", table="selected_strategies", column=col_name)
                except Exception as e:
                    logger.warning("migration_failed_to_add_column", column=col_name, error=str(e))


def reset_engine() -> None:
    """Reset the engine and session factory (useful for testing)."""
    for engine in _engines.values():
        engine.dispose()
    _engines.clear()
    _SessionFactories.clear()
