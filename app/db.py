"""SQLAlchemy engine / session / Base.

SQLite is used locally; swap PORTAL_DATABASE_URL for Postgres later and the rest
of the code is unchanged.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL

# check_same_thread=False so FastAPI's threadpool can share the SQLite engine.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """Create tables if they don't exist. Safe to call repeatedly."""
    from . import models  # noqa: F401  (register models on Base)
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency yielding a session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
