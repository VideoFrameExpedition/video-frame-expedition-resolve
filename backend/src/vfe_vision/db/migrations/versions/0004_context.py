"""capture context: place, sun, weather, online-service cache

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _video_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["video_id"], ["videos.id"], name=op.f(f"fk_{table}_video_id_videos"), ondelete="CASCADE"
    )


def upgrade() -> None:
    op.create_table(
        "context_place",
        sa.Column("video_id", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=300), nullable=True),
        sa.Column("locality", sa.String(length=200), nullable=True),
        sa.Column("region", sa.String(length=200), nullable=True),
        sa.Column("country", sa.String(length=100), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        _video_fk("context_place"),
        sa.PrimaryKeyConstraint("video_id", name=op.f("pk_context_place")),
    )
    with op.batch_alter_table("context_place", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_context_place_country_code"), ["country_code"], unique=False
        )

    op.create_table(
        "context_sun",
        sa.Column("video_id", sa.String(length=32), nullable=False),
        sa.Column("at_utc", sa.DateTime(), nullable=False),
        sa.Column("elevation_deg", sa.Double(), nullable=False),
        sa.Column("azimuth_deg", sa.Double(), nullable=False),
        sa.Column("light_phase", sa.String(length=24), nullable=True),
        sa.Column("twilight_phase", sa.String(length=24), nullable=True),
        sa.Column("day_part", sa.String(length=16), nullable=True),
        sa.Column("theoretical_cct_k", sa.Integer(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        _video_fk("context_sun"),
        sa.PrimaryKeyConstraint("video_id", name=op.f("pk_context_sun")),
    )
    with op.batch_alter_table("context_sun", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_context_sun_light_phase"), ["light_phase"], unique=False
        )

    op.create_table(
        "context_weather",
        sa.Column("video_id", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("at_utc", sa.DateTime(), nullable=False),
        sa.Column("weather_code", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(length=16), nullable=True),
        sa.Column("temperature_c", sa.Double(), nullable=True),
        sa.Column("cloud_cover_pct", sa.Double(), nullable=True),
        sa.Column("provisional", sa.Boolean(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        _video_fk("context_weather"),
        sa.PrimaryKeyConstraint("video_id", name=op.f("pk_context_weather")),
    )
    with op.batch_alter_table("context_weather", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_context_weather_category"), ["category"], unique=False
        )

    op.create_table(
        "service_cache",
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("service", sa.String(length=32), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_service_cache")),
    )
    with op.batch_alter_table("service_cache", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_service_cache_service"), ["service"], unique=False)


def downgrade() -> None:
    for table, index in (
        ("service_cache", "ix_service_cache_service"),
        ("context_weather", "ix_context_weather_category"),
        ("context_sun", "ix_context_sun_light_phase"),
        ("context_place", "ix_context_place_country_code"),
    ):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_index(batch_op.f(index))
        op.drop_table(table)
