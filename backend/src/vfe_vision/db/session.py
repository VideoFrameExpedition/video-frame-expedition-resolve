"""SQLite engine and session factories.

Two processes write to the database (API and worker). In WAL mode a *deferred* transaction that
reads and then writes fails immediately with ``SQLITE_BUSY`` — ``busy_timeout`` does not help —
so every write session starts with ``BEGIN IMMEDIATE`` while read sessions use a plain ``BEGIN``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

_IMMEDIATE = "vfe_immediate"


def create_sqlite_engine(db_path: Path) -> Engine:
    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_pre_ping=False,
    )

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: sqlite3.Connection, _record: Any) -> None:
        # Take over transaction control from the sqlite3 module (see the "begin" hook below).
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _on_begin(conn: Any) -> None:
        immediate = conn.get_execution_options().get(_IMMEDIATE, False)
        conn.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")

    return engine


class Database:
    """Owns the engine; hands out short-lived read or write sessions."""

    def __init__(self, db_path: Path) -> None:
        self.path = db_path
        self.engine = create_sqlite_engine(db_path)
        self._read = sessionmaker(bind=self.engine, expire_on_commit=False)
        self._write = sessionmaker(
            bind=self.engine.execution_options(**{_IMMEDIATE: True}), expire_on_commit=False
        )

    @contextmanager
    def read(self) -> Iterator[Session]:
        """Read-only session (the transaction is rolled back on exit)."""
        with self._read() as session:
            try:
                yield session
            finally:
                # Detach first: a rollback would otherwise expire the loaded objects that
                # callers keep using after the session is closed.
                session.expunge_all()
                session.rollback()

    @contextmanager
    def write(self) -> Iterator[Session]:
        """Write session: ``BEGIN IMMEDIATE``, commit on success, rollback on error."""
        with self._write() as session:
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    def dispose(self) -> None:
        self.engine.dispose()
