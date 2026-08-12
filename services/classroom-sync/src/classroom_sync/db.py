"""Database engine and transaction helpers for the classroom sync service."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def create_database_engine(database_url: str) -> Engine:
    """Build an explicit engine without relying on a process-global connection."""

    return create_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return the session factory used by request handlers and workers."""

    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def transactional_session(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    """Provide a transaction that commits once or rolls back on any error."""

    with session_factory.begin() as session:
        yield session
