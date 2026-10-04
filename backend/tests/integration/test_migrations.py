"""The Alembic history must produce exactly the schema declared by the ORM models."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

from vfe_vision.db import models  # noqa: F401 - registers the tables on the metadata
from vfe_vision.db.base import Base
from vfe_vision.db.migrate import (
    alembic_config,
    current_revision,
    head_revision,
    upgrade_database,
)


def include_object(
    obj: object, name: str | None, type_: str, reflected: bool, other: object
) -> bool:
    # Same filter as migrations/env.py: FTS5 shadow tables are created by raw SQL.
    return not (type_ == "table" and (name or "").startswith("search_fts"))


def test_migrations_match_the_models(tmp_path: Path) -> None:
    db_path = tmp_path / "schema.sqlite3"
    upgrade_database(db_path, tmp_path / "backups")
    assert current_revision(db_path) == head_revision(db_path)
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(
                conn, opts={"include_object": include_object, "compare_type": False}
            )
            diff = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()
    assert diff == []


def test_skips_caused_by_the_video_are_marked_settled(tmp_path: Path) -> None:
    db_path = tmp_path / "skips.sqlite3"
    command.upgrade(alembic_config(db_path), "0008")
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with engine.begin() as conn:
            # Foreign keys are not enforced on this plain connection: runs alone suffice.
            for run_id, reason in (
                ("1", "Lisible directement par le navigateur"),
                ("2", "Services en ligne désactivés (mode hors-ligne)"),
            ):
                conn.execute(
                    sa.text(
                        "INSERT INTO stage_runs (id, video_id, stage, stage_version, cache_key, "
                        "status, skip_reason, summary, attempts, created_at) VALUES "
                        "(:id, 'v', 'proxy', 1, :id, 'skipped', :reason, "
                        "'{\"retryable\": false}', 1, '2026-01-01')"
                    ),
                    {"id": run_id, "reason": reason},
                )
        command.upgrade(alembic_config(db_path), "head")
        with engine.connect() as conn:
            summaries: dict[str, str] = dict(
                conn.execute(sa.text("SELECT id, summary FROM stage_runs")).all()
            )
    finally:
        engine.dispose()
    assert summaries == {
        "1": '{"retryable":false,"permanent":true}',  # the video itself: settled
        "2": '{"retryable": false}',  # a setting: checked again by an ordinary analysis
    }
