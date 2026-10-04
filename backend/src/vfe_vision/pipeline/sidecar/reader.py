"""Import the analysis file of a video before analysing it.

A video has one file per language: the one in the analysis language of the
application is imported (else the other one, else the single file of earlier versions), and the
other gives back the translations, text by text.

Only for a video without any finished analysis, and only when the file describes this very
content (fingerprint and size). Everything is inserted in one transaction, with the stage runs
and their keys, so that an ordinary analysis (« Complete ») keeps what was imported and only
does what is missing. The images are extracted again from the video, as the stages make them:
keyframes and thumbnails at their times, extra frames of the shot stories at theirs.

The rows keep the ids of the file when none of them is taken in this library, so that every
key stays the same, even for « Update ». When the same video is in the library already
with those ids, the rows get new ids, and the stages whose input facts name rows get their
input key computed again from the imported rows (``Stage.facts_name_rows``).
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from vfe_vision.core.errors import CancelledError, VfeError
from vfe_vision.core.ids import new_id
from vfe_vision.db.base import Base
from vfe_vision.db.models import (
    Keyframe,
    Shot,
    StageRun,
    Transcript,
    Video,
    VideoMetadata,
    VideoSynthesis,
)
from vfe_vision.db.session import Database
from vfe_vision.db.translations import store_entries
from vfe_vision.domain.enums import StageStatus
from vfe_vision.domain.sidecar import (
    import_problem,
    is_ours,
    paired,
    story_keyframes,
    synthesis_data,
)
from vfe_vision.domain.translation import LANGUAGES
from vfe_vision.pipeline.registry import StageRegistry
from vfe_vision.pipeline.sidecar.tables import (
    FILE_FIELDS,
    OWNER,
    RUN_LEFT_OUT,
    TABLES,
    USER_FIELDS,
    VIDEO_LEFT_OUT,
    Table,
    columns,
    from_json,
    load,
)
from vfe_vision.pipeline.sidecar.writer import read_document, spellings
from vfe_vision.pipeline.stage import StageContext, input_key
from vfe_vision.pipeline.stages.keyframes import (
    SHOT_FRAMES_DIR,
    FrameLook,
    KeyframeFiles,
    keyframe_at,
    pick_poster,
)
from vfe_vision.pipeline.stages.synthesis import input_key as synthesis_key
from vfe_vision.pipeline.stages.vision_shots import extract_story_frame
from vfe_vision.pipeline.synthesis_facts import load_facts

UNFINISHED = frozenset({StageStatus.PENDING, StageStatus.RUNNING, StageStatus.CACHED})
_UNREADABLE = (VfeError, OSError, ValueError, TypeError, KeyError, sa.exc.SQLAlchemyError)


@dataclass(frozen=True, slots=True)
class ImportReport:
    path: Path
    imported: bool
    reason: str | None = None  # why the file was left aside
    keyframes: int = 0
    stages: int = 0
    fresh_ids: bool = False  # the same video is in the library already: rows got new ids
    translations: int = 0  # texts given back in the other language by its file


class _LeftAsideError(VfeError):
    """The file cannot be imported: the analysis goes on as if there were none."""


def has_results(db: Database, video_id: str) -> bool:
    """Any finished analysis of the video (a stage that succeeded): a file never replaces it."""
    with db.read() as session:
        return _analysed(session, video_id)


def _analysed(session: Session, video_id: str) -> bool:
    found = session.execute(
        sa.select(StageRun.id)
        .where(StageRun.video_id == video_id, StageRun.status == StageStatus.SUCCEEDED)
        .limit(1)
    ).first()
    if found is not None:
        return True
    # Rows without a finished run are not expected; they are never overwritten either.
    for model in (Keyframe, Shot, VideoMetadata, Transcript, VideoSynthesis):
        owner = columns(model)[OWNER]
        if session.execute(sa.select(owner).where(owner == video_id).limit(1)).first():
            return True
    return False


def find_sidecars(video: Path, preferred: str) -> list[tuple[str | None, Path]]:
    """The video's analysis files, the one to import first: in ``preferred``, in the other
    language, then the single file of earlier versions (language None); each under the name
    the rule gives now or its other spelling."""
    order: list[str | None] = [preferred, *(lang for lang in LANGUAGES if lang != preferred)]
    found: list[tuple[str | None, Path]] = []
    for language in [*order, None]:
        for candidate in spellings(video, language):
            if candidate.is_file():
                found.append((language, candidate))
                break
    return found


def find_sidecar(video: Path, preferred: str = "fr") -> Path | None:
    """The analysis file imported first (see ``find_sidecars``)."""
    found = find_sidecars(video, preferred)
    return found[0][1] if found else None


def import_sidecar(ctx: StageContext, registry: StageRegistry) -> ImportReport | None:
    """Import the video's analysis file when the video has no analysis yet.

    None: nothing to import (no file, or analyses already there). A file that does not describe
    this video, or cannot be read, is left aside with its reason.
    """
    db = ctx.tools.db
    if has_results(db, ctx.video.id):
        return None
    try:
        files = find_sidecars(ctx.video.path, ctx.prefs.language)
    except OSError:  # the folder is out of reach: the analysis will say so
        return None
    if not files:
        return None
    with db.read() as session:
        size = session.get_one(Video, ctx.video.id).size_bytes
    refused: ImportReport | None = None
    for position, (_language, path) in enumerate(files):
        try:
            document = read_document(path)
        except OSError as exc:
            refused = refused or ImportReport(
                path, imported=False, reason=f"lecture impossible : {exc}"
            )
            continue
        problem = (
            import_problem(document, fingerprint=ctx.video.fingerprint, size_bytes=size)
            if document is not None
            else "ce n'est pas un fichier JSON"
        )
        if problem is not None:
            refused = refused or ImportReport(path, imported=False, reason=problem)
            continue
        others = [other for other in files[position + 1 :] if other[0] is not None]
        return _Import(ctx, registry, path).run(document, _pairs(document, others))
    return refused


def _pairs(
    document: Mapping[str, Any], others: list[tuple[str | None, Path]]
) -> list[tuple[str, str, str]]:
    """The texts of ``document`` in another language, read from the file of that language
    written with it: ``(text, language, translation)``, the same words in both left out (the
    translation stage asks for them again, as it must for a text it could not translate)."""
    found: list[tuple[str, str, str]] = []
    for language, path in others:
        if language is None or language == document.get("language"):
            continue
        try:
            other = read_document(path)
        except OSError:
            continue
        if not is_ours(other):
            continue
        found += [(a, language, b) for a, b in paired(document, other) if a.strip() != b.strip()]
    return found


# ---------------------------------------------------------------- the file's content
@dataclass(frozen=True, slots=True)
class _Content:
    video: dict[str, Any]
    user: dict[str, Any]
    runs: list[dict[str, Any]]
    tables: dict[str, list[dict[str, Any]]]  # every table as a list of rows

    @classmethod
    def of(cls, document: Mapping[str, Any]) -> _Content:
        user = document.get("user")
        return cls(
            video=dict(document["video"]),
            user=dict(user) if isinstance(user, dict) else {},
            runs=_rows(document, "stage_runs", many=True),
            tables={table.key: _rows(document, table.key, many=table.many) for table in TABLES},
        )


def _rows(document: Mapping[str, Any], key: str, *, many: bool) -> list[dict[str, Any]]:
    value = document.get(key)
    if value is None:
        return []
    rows = value if many else [value]
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise _LeftAsideError(f"contenu illisible : « {key} »")
    return rows


def _ref(mapping: Mapping[str, str], value: Any) -> str | None:
    return mapping.get(value) if isinstance(value, str) else None


@dataclass(slots=True)
class _Ids:
    shots: dict[str, str] = field(default_factory=dict)  # id in the file → id here
    keyframes: dict[str, str] = field(default_factory=dict)
    fresh: bool = False


@dataclass(frozen=True, slots=True)
class _Frame:
    image_path: str
    thumb_path: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class _Story:
    frame_times: list[float]
    keyframe_ids: list[str | None]
    frame_paths: list[str]


# ---------------------------------------------------------------- the import
class _Import:
    def __init__(self, ctx: StageContext, registry: StageRegistry, path: Path) -> None:
        self.ctx = ctx
        self.registry = registry
        self.path = path
        self.content = _Content({}, {}, [], {table.key: [] for table in TABLES})
        self.ids = _Ids()
        self.frames: dict[str, _Frame] = {}  # by keyframe id in the file
        self.stories: list[_Story | None] = []  # in the order of the file's stories
        self.files: KeyframeFiles | None = None
        self.story_dir: Path | None = None

    def run(
        self, document: Mapping[str, Any], pairs: list[tuple[str, str, str]] | None = None
    ) -> ImportReport:
        self.ctx.progress(0.0, f"Reprise des analyses de « {self.path.name} »")
        # A file of one language: its texts may differ from those its stages read.
        in_language = isinstance(document.get("language"), str)
        try:
            self.content = _Content.of(document)
            self.ids = self._link()
            look = self._look()
            self._keyframe_images(look)
            self._story_frames(look)
            stages = self._store(pairs or [])
        except CancelledError:
            self._discard()
            raise
        except _UNREADABLE as exc:
            self._discard()
            reason = exc.detail if isinstance(exc, VfeError) else f"contenu illisible ({exc!r})"
            return ImportReport(self.path, imported=False, reason=reason)
        if self.files is not None:
            self.files.keep()
        keep = self.story_dir.name if self.story_dir is not None else ""
        self.ctx.tools.artifacts.prune(self.ctx.video.id, SHOT_FRAMES_DIR, keep=keep)
        if self.ids.fresh or in_language:
            try:
                self._rekey(texts=in_language)
            except Exception:  # the import stands: those stages are only redone by « Complete »
                self.ctx.log.exception("input keys not computed again after an import")
        return ImportReport(
            self.path,
            imported=True,
            keyframes=len(self.frames),
            stages=stages,
            fresh_ids=self.ids.fresh,
            translations=len(pairs or []),
        )

    # ------------------------------------------------------------ ids
    def _link(self) -> _Ids:
        """Keep the file's ids of shots and keyframes unless one is taken in this library."""
        shots = [str(row["id"]) for row in self.content.tables["shots"]]
        keyframes = [str(row["id"]) for row in self.content.tables["keyframes"]]
        with self.ctx.tools.db.read() as session:
            taken = any(
                session.execute(sa.select(model.id).where(model.id.in_(ids)).limit(1)).first()
                for model, ids in ((Shot, shots), (Keyframe, keyframes))
                if ids
            )

        def rename(old: str) -> str:
            return new_id() if taken else old

        return _Ids(
            shots={old: rename(old) for old in shots},
            keyframes={old: rename(old) for old in keyframes},
            fresh=taken,
        )

    def _look(self) -> FrameLook:
        video = self.content.video
        return FrameLook.of(
            video.get("is_hdr"), video.get("hdr_peak_nits"), video.get("color_profile")
        )

    # ------------------------------------------------------------ images
    def _keyframe_images(self, look: FrameLook) -> None:
        """Every keyframe and its thumbnail, extracted again at its time."""
        rows = sorted(self.content.tables["keyframes"], key=lambda row: int(row["idx"]))
        if not rows:
            return
        self.files = KeyframeFiles(self.ctx.tools.artifacts, self.ctx.video.id)
        for position, row in enumerate(rows):
            self.ctx.cancel.raise_if_cancelled()
            t_s = float(row["t_s"])
            try:
                image = keyframe_at(
                    self.ctx.tools.ffmpeg, self.ctx.video.path, t_s, look,
                    self.files.frames_dir, cancel=self.ctx.cancel,
                )  # fmt: skip
            except CancelledError:
                raise
            except (VfeError, OSError, ValueError) as exc:
                self.ctx.log.warning("keyframe not extracted", t_s=t_s, error=str(exc))
                raise _LeftAsideError(
                    f"l'image clé à {t_s:.3f} s ne peut pas être extraite de la vidéo"
                ) from exc
            image_path, thumb_path = self.files.write(int(row["idx"]), image)
            height, width = image.shape[:2]
            self.frames[str(row["id"])] = _Frame(image_path, thumb_path, width, height)
            self.ctx.progress(0.0, f"Images clés reprises : {position + 1}/{len(rows)}")

    def _story_frames(self, look: FrameLook) -> None:
        """The frames of each shot story: a keyframe's thumbnail, or a frame extracted again
        at its time (a frame that cannot be is left out of its story)."""
        thumbs = {self.ids.keyframes[old]: frame.thumb_path for old, frame in self.frames.items()}
        for row in self.content.tables["shot_stories"]:
            times = [float(t) for t in row.get("frame_times") or []]
            linked = story_keyframes(row.get("keyframe_ids") or [], self.ids.keyframes)
            story = _Story([], [], [])
            for t_s, keyframe_id in zip(times, linked, strict=True):
                path = thumbs.get(keyframe_id) if keyframe_id else self._story_frame(t_s, look)
                if path is None:
                    continue
                story.frame_times.append(t_s)
                story.keyframe_ids.append(keyframe_id if keyframe_id in thumbs else None)
                story.frame_paths.append(path)
            self.stories.append(story if story.frame_times else None)

    def _story_frame(self, t_s: float, look: FrameLook) -> str | None:
        self.ctx.cancel.raise_if_cancelled()
        store = self.ctx.tools.artifacts
        if self.story_dir is None:
            self.story_dir = store.subdir(self.ctx.video.id, f"{SHOT_FRAMES_DIR}/{new_id()}")
        try:
            path = extract_story_frame(
                self.ctx.tools.ffmpeg, self.ctx.video.path, t_s, self.story_dir, look,
                cancel=self.ctx.cancel,
            )  # fmt: skip
        except CancelledError:
            raise
        except (VfeError, OSError, ValueError) as exc:
            self.ctx.log.warning("story frame not extracted", t_s=t_s, error=str(exc))
            return None
        return store.rel(path)

    def _discard(self) -> None:
        if self.files is not None:
            self.files.discard()
        if self.story_dir is not None:
            shutil.rmtree(self.story_dir, ignore_errors=True)

    # ------------------------------------------------------------ rows
    def _store(self, pairs: list[tuple[str, str, str]]) -> int:
        """One transaction: the video's analysis columns, every row, the stage runs, the
        translations given back by the file of the other language."""
        video_id = self.ctx.video.id
        with self.ctx.tools.db.write() as session:
            if _analysed(session, video_id):  # analysed meanwhile: never replaced
                raise _LeftAsideError("la vidéo a déjà des analyses")
            video = session.get_one(Video, video_id)
            self._video(video)
            keyframes: list[Keyframe] = []
            for table in TABLES:  # flushed table by table: a row after the rows it names
                rows = self._rows(table)
                session.add_all(rows)
                session.flush()
                keyframes += [row for row in rows if isinstance(row, Keyframe)]
            poster = pick_poster(keyframes, video.duration_s)
            video.poster_path = poster.thumb_path if poster else None
            runs = self._runs()
            session.add_all(runs)
            store_entries(session, pairs, model=None)
        self.ctx.video.duration_s = video.duration_s  # the probe is not run again
        return len(runs)

    def _rows(self, table: Table) -> list[Base]:
        link = _LINKS.get(table.key)
        owned = OWNER in columns(table.model)
        left_out = table.left_out | {OWNER}
        rows: list[Base] = []
        for position, data in enumerate(self.content.tables[table.key]):
            fixed = link(self, data, position) if link is not None else {}
            if fixed is None:  # it names a row the file does not have
                continue
            if owned:
                fixed[OWNER] = self.ctx.video.id
            rows.append(load(table.model, data, left_out, **fixed))
        return rows

    def _shot(self, data: dict[str, Any], position: int) -> dict[str, Any] | None:
        return {"id": self.ids.shots[str(data["id"])]}

    def _keyframe(self, data: dict[str, Any], position: int) -> dict[str, Any] | None:
        old = str(data["id"])
        frame = self.frames[old]
        return {
            "id": self.ids.keyframes[old],
            "shot_id": _ref(self.ids.shots, data.get("shot_id")),
            "duplicate_of": _ref(self.ids.keyframes, data.get("duplicate_of")),
            "image_path": frame.image_path,
            "thumb_path": frame.thumb_path,
            "width": frame.width,
            "height": frame.height,
        }

    def _of_keyframe(self, data: dict[str, Any], position: int) -> dict[str, Any] | None:
        """A row about one keyframe: description, box, scan, text read."""
        keyframe = _ref(self.ids.keyframes, data.get("keyframe_id"))
        return None if keyframe is None else {"keyframe_id": keyframe}

    def _story(self, data: dict[str, Any], position: int) -> dict[str, Any] | None:
        shot = _ref(self.ids.shots, data.get("shot_id"))
        story = self.stories[position]
        if shot is None or story is None:
            return None
        return {
            "shot_id": shot,
            "frame_times": story.frame_times,
            "keyframe_ids": story.keyframe_ids,
            "frame_paths": story.frame_paths,
        }

    def _synthesis(self, data: dict[str, Any], position: int) -> dict[str, Any] | None:
        stored = data.get("data")
        blocks = synthesis_data(stored if isinstance(stored, dict) else {}, self.ids.keyframes)
        return {"data": blocks}

    def _runs(self) -> list[StageRun]:
        """The latest run of each stage, with its keys. Left out: runs of stages this version
        does not know, and the successes whose result is not in the file (the viewing copy, the
        search index: made again by the job; files of earlier versions still name them). A run
        that had not finished is an interrupted one."""
        stages = {stage.name: stage for stage in self.registry.plan()}
        runs: list[StageRun] = []
        for data in self.content.runs:
            stage = stages.get(str(data.get("stage")))
            if stage is None:
                continue
            run = load(StageRun, data, RUN_LEFT_OUT | {"created_at"}, video_id=self.ctx.video.id)
            if stage.rebuilt_after_import and run.status == StageStatus.SUCCEEDED:
                continue
            if run.status in UNFINISHED:
                run.status = StageStatus.CANCELLED
                run.error = "Interrompue"
            runs.append(run)
        return runs

    def _video(self, video: Video) -> None:
        """The analysis columns of the video; the user's fields only where still empty."""
        video_columns = columns(Video)
        imported = set(video_columns) - VIDEO_LEFT_OUT - FILE_FIELDS - set(USER_FIELDS)
        for name in imported & self.content.video.keys():
            setattr(video, name, from_json(video_columns[name], self.content.video[name]))
        for name in USER_FIELDS:
            if name in self.content.user and getattr(video, name) in (None, False, ""):
                setattr(video, name, from_json(video_columns[name], self.content.user[name]))

    # ------------------------------------------------------------ keys
    def _rekey(self, *, texts: bool = False) -> None:
        """New ids: the stages whose input facts name rows get their input key from the
        imported rows, which they describe (the file is one consistent snapshot). ``texts``: a
        file of one language, whose texts may be translations: so do the stages that
        read texts, and the synthesis says it is written from them."""
        ctx = self.ctx
        with ctx.tools.db.read() as session:
            runs = session.execute(
                sa.select(StageRun.id, StageRun.stage).where(
                    StageRun.video_id == ctx.video.id,
                    StageRun.status == StageStatus.SUCCEEDED,
                    StageRun.input_key.is_not(None),
                    StageRun.cache_key != "",
                )
            ).all()
        keys: dict[str, str] = {}
        for run_id, name in runs:
            stage = self.registry.get(name)
            if (self.ids.fresh and stage.facts_name_rows) or (texts and stage.facts_read_texts):
                facts = stage.input_facts(ctx)
                keys[run_id] = input_key(stage, fingerprint=ctx.video.fingerprint, facts=facts)
        written: str | None = None
        if texts:
            with ctx.tools.db.read() as session:
                stored = session.get(VideoSynthesis, ctx.video.id)
                language = stored.language if stored is not None else None
            if language is not None:
                written = synthesis_key(load_facts(ctx.tools.db, ctx.video.id), language)
        with ctx.tools.db.write() as session:
            for run_id, key in keys.items():
                session.execute(
                    sa.update(StageRun).where(StageRun.id == run_id).values(input_key=key)
                )
            if written is not None:
                session.execute(
                    sa.update(VideoSynthesis)
                    .where(VideoSynthesis.video_id == ctx.video.id)
                    .values(input_key=written)
                )


_Link = Callable[[_Import, dict[str, Any], int], dict[str, Any] | None]
# How the rows of a table are linked again to the video's other rows (by default: owned only).
_LINKS: dict[str, _Link] = {
    "shots": _Import._shot,
    "keyframes": _Import._keyframe,
    "frame_analyses": _Import._of_keyframe,
    "detections": _Import._of_keyframe,
    "subject_scans": _Import._of_keyframe,
    "ocr_texts": _Import._of_keyframe,
    "shot_stories": _Import._story,
    "video_synthesis": _Import._synthesis,
}
