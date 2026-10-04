"""Programmatic Alembic migrations, with a ``VACUUM INTO`` backup before any schema change."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from vfe_vision.core.logging import get_logger

log = get_logger(__name__)
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(db_path: Path) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", MIGRATIONS_DIR.as_posix())
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    return cfg


def current_revision(db_path: Path) -> str | None:
    if not db_path.exists():
        return None
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def head_revision(db_path: Path) -> str | None:
    return ScriptDirectory.from_config(alembic_config(db_path)).get_current_head()


def upgrade_database(db_path: Path, backups_dir: Path) -> None:
    """Bring the schema to the latest revision, backing up an existing database first."""
    current = current_revision(db_path)
    head = head_revision(db_path)
    if current == head:
        return
    if current is not None:
        backups_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = backups_dir / f"vfe-{stamp}-{current}.sqlite3"
        with sqlite3.connect(db_path) as conn:
            conn.execute("VACUUM INTO ?", (str(backup),))
        log.info("database backed up before migration", backup=str(backup))
    log.info("upgrading database schema", current=current, head=head)
    command.upgrade(alembic_config(db_path), "head")
