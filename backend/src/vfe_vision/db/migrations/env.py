"""Alembic environment.

Migrations run on a plain engine *without* ``PRAGMA foreign_keys=ON``: batch migrations recreate
tables on SQLite, and dropping a referenced table with enforcement on would cascade deletes.
"""

from __future__ import annotations

from typing import Any, Literal

from alembic import context
from alembic.autogenerate.api import AutogenContext
from sqlalchemy import create_engine

from vfe_vision.db import models  # noqa: F401 - registers the tables on the metadata
from vfe_vision.db.base import Base

config = context.config
target_metadata = Base.metadata

# Virtual tables and their shadow tables are managed by hand-written migrations.
_UNMANAGED_PREFIXES = ("search_fts",)


def include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    return not (type_ == "table" and name is not None and name.startswith(_UNMANAGED_PREFIXES))


def render_item(type_: str, obj: Any, autogen_context: AutogenContext) -> str | Literal[False]:
    """Render custom column types with their plain SQLAlchemy storage type."""
    from vfe_vision.db.base import UTCDateTime

    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime()"
    return False


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
        include_object=include_object,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = config.get_main_option("sqlalchemy.url")
    assert url is not None
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            include_object=include_object,
            render_item=render_item,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
