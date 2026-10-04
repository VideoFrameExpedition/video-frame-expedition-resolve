"""Long-lived collaborators of the API process."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from vfe_vision.adapters.embeddings.gemma import EmbedderLoader
from vfe_vision.adapters.ffmpeg.tools import Ffmpeg
from vfe_vision.adapters.lmstudio.client import LmStudioClient
from vfe_vision.adapters.models.store import ModelStore
from vfe_vision.adapters.resolve.builder import ResolveTimelineBuilder
from vfe_vision.adapters.resolve.editor import ResolveEditor
from vfe_vision.adapters.resolve.reader import ResolveReader, TimelineSnapshots
from vfe_vision.core.config import Settings
from vfe_vision.db.lmstudio_link import LinkReader
from vfe_vision.db.preferences import load_preferences
from vfe_vision.db.session import Database
from vfe_vision.db.translations import TranslationCache
from vfe_vision.domain.lmstudio_link import LmStudioTarget
from vfe_vision.ports.embeddings import EmbedderSource, no_embedder
from vfe_vision.ports.resolve import ResolveSource, ResolveTimelineEditor, ResolveTimelineMaker
from vfe_vision.services.search_vectors import VectorCache
from vfe_vision.storage.artifacts import ArtifactStore

LMSTUDIO_TEST_TIMEOUT_S = 8.0


def _lmstudio_probe(target: LmStudioTarget) -> LmStudioClient:
    """A client for one question to an LM Studio that is not (yet) the one in use."""
    return LmStudioClient(target.url, token=target.token, timeout_s=LMSTUDIO_TEST_TIMEOUT_S)


@dataclass(slots=True)
class AppContainer:
    settings: Settings
    db: Database
    artifacts: ArtifactStore
    ffmpeg: Ffmpeg
    lmstudio: LmStudioClient
    worker_pid: int | None = None
    embedder: EmbedderSource = no_embedder  # queries of the search by meaning
    vectors: VectorCache = field(default_factory=VectorCache)
    # The analyses' texts in French and in English, read again when they change.
    translations: TranslationCache = field(default_factory=TranslationCache)
    # DaVinci Resolve, read only: the reader of the running Resolve (tests assign a
    # fake), and the timelines read for a preview, reused by the import that follows.
    resolve: ResolveSource = field(init=False)
    timeline_snapshots: TimelineSnapshots = field(default_factory=TimelineSnapshots)
    # The one change made in Resolve, on request: a timeline of chosen videos.
    resolve_builder: ResolveTimelineMaker = field(init=False)
    # The assistant's Resolve tools, when allowed: new timelines and markers.
    resolve_editor: ResolveTimelineEditor = field(init=False)
    # Where LM Studio runs: the address chosen on the System page, and how an
    # address is tried before it is chosen (tests assign a fake).
    lmstudio_link: LinkReader = field(init=False)
    lmstudio_probe: Callable[[LmStudioTarget], LmStudioClient] = _lmstudio_probe

    def __post_init__(self) -> None:
        token = self.settings.lmstudio_token
        self.lmstudio_link = LinkReader(
            self.db, self.settings.lmstudio_url, token.get_secret_value() if token else None
        )

        def host() -> str | None:
            return load_preferences(self.db).resolve_host

        self.resolve = ResolveReader(self.settings, host=host)
        self.resolve_builder = ResolveTimelineBuilder(self.settings, host=host)
        self.resolve_editor = ResolveEditor(self.settings, host=host)

    @classmethod
    def create(cls, settings: Settings) -> AppContainer:
        token = settings.lmstudio_token.get_secret_value() if settings.lmstudio_token else None
        db = Database(settings.db_path)
        link = LinkReader(db, settings.lmstudio_url, token)
        container = cls(
            settings=settings,
            db=db,
            artifacts=ArtifactStore(settings.artifacts_dir),
            ffmpeg=Ffmpeg(settings.ffmpeg_path, settings.ffprobe_path),
            lmstudio=LmStudioClient(link, timeout_s=30.0),
            embedder=EmbedderLoader(ModelStore(settings.models_dir)),
        )
        container.lmstudio_link = link  # the one its client reads
        return container

    async def aclose(self) -> None:
        await self.lmstudio.aclose()
        self.db.dispose()
