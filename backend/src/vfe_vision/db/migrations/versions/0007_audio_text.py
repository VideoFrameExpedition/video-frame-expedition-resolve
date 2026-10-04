"""audio and text: transcripts, sound scene and events, on-screen text

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    ref_table = target.split(".")[0]
    return sa.ForeignKeyConstraint(
        [column], [target], name=op.f(f"fk_{table}_{column}_{ref_table}"), ondelete="CASCADE"
    )


def upgrade() -> None:
    with op.batch_alter_table("videos", schema=None) as batch_op:
        batch_op.add_column(sa.Column("transcript_mode", sa.String(length=8), nullable=True))
        batch_op.add_column(sa.Column("transcript_language", sa.String(length=8), nullable=True))

    op.create_table(
        "transcripts",
        sa.Column("video_id", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("language", sa.String(length=8), nullable=True),
        sa.Column("language_probability", sa.Double(), nullable=True),
        sa.Column("languages", sa.JSON(), nullable=False),
        sa.Column("duration_s", sa.Double(), nullable=True),
        sa.Column("speech_s", sa.Double(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("segment_count", sa.Integer(), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        _fk("transcripts", "video_id", "videos.id"),
        sa.PrimaryKeyConstraint("video_id", name=op.f("pk_transcripts")),
    )
    with op.batch_alter_table("transcripts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_transcripts_language"), ["language"], unique=False)

    op.create_table(
        "transcript_segments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("video_id", sa.String(length=32), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("start_s", sa.Double(), nullable=False),
        sa.Column("end_s", sa.Double(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=True),
        sa.Column("avg_logprob", sa.Double(), nullable=True),
        sa.Column("no_speech_prob", sa.Double(), nullable=True),
        sa.Column("compression_ratio", sa.Double(), nullable=True),
        sa.Column("temperature", sa.Double(), nullable=True),
        sa.Column("suspect", sa.Boolean(), nullable=False),
        sa.Column("second_pass", sa.Boolean(), nullable=False),
        sa.Column("words", sa.JSON(), nullable=False),
        _fk("transcript_segments", "video_id", "transcripts.video_id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transcript_segments")),
        sa.UniqueConstraint("video_id", "idx", name=op.f("uq_transcript_segments_video_id")),
    )
    with op.batch_alter_table("transcript_segments", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_transcript_segments_video_id"), ["video_id"], unique=False
        )

    op.create_table(
        "audio_scene",
        sa.Column("video_id", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("speech_s", sa.Double(), nullable=True),
        sa.Column("music_s", sa.Double(), nullable=True),
        sa.Column("dominant", sa.String(length=24), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        _fk("audio_scene", "video_id", "videos.id"),
        sa.PrimaryKeyConstraint("video_id", name=op.f("pk_audio_scene")),
    )
    with op.batch_alter_table("audio_scene", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_audio_scene_dominant"), ["dominant"], unique=False)

    op.create_table(
        "audio_segments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("video_id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("category", sa.String(length=24), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column("start_s", sa.Double(), nullable=False),
        sa.Column("end_s", sa.Double(), nullable=False),
        sa.Column("score", sa.Double(), nullable=True),
        _fk("audio_segments", "video_id", "videos.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audio_segments")),
    )
    with op.batch_alter_table("audio_segments", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_audio_segments_category"), ["category"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_audio_segments_video_id"), ["video_id"], unique=False
        )

    op.create_table(
        "ocr_texts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("keyframe_id", sa.String(length=32), nullable=False),
        sa.Column("video_id", sa.String(length=32), nullable=False),
        sa.Column("t_s", sa.Double(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("score", sa.Double(), nullable=False),
        sa.Column("box", sa.JSON(), nullable=False),
        sa.Column("engine", sa.String(length=64), nullable=False),
        _fk("ocr_texts", "keyframe_id", "keyframes.id"),
        _fk("ocr_texts", "video_id", "videos.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ocr_texts")),
    )
    with op.batch_alter_table("ocr_texts", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ocr_texts_keyframe_id"), ["keyframe_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_ocr_texts_video_id"), ["video_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("ocr_texts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ocr_texts_video_id"))
        batch_op.drop_index(batch_op.f("ix_ocr_texts_keyframe_id"))
    op.drop_table("ocr_texts")
    with op.batch_alter_table("audio_segments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audio_segments_video_id"))
        batch_op.drop_index(batch_op.f("ix_audio_segments_category"))
    op.drop_table("audio_segments")
    with op.batch_alter_table("audio_scene", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audio_scene_dominant"))
    op.drop_table("audio_scene")
    with op.batch_alter_table("transcript_segments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_transcript_segments_video_id"))
    op.drop_table("transcript_segments")
    with op.batch_alter_table("transcripts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_transcripts_language"))
    op.drop_table("transcripts")
    with op.batch_alter_table("videos", schema=None) as batch_op:
        batch_op.drop_column("transcript_language")
        batch_op.drop_column("transcript_mode")
