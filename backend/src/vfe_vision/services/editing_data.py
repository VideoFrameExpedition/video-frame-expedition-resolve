"""Everything the editing helpers read about one video, loaded once: the video,
the facts of its synthesis (shots, words, keyframes), the synthesis as shown (chapters,
highlights, usability, suggestions), the shots with their stories, and the beings seen.

Shared by the exports, the Resolve payload and ``match_clips``: each would otherwise read the
same rows several times per video.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from vfe_vision.db.models import Shot, ShotStory, Video
from vfe_vision.db.timeline_bins import resolve_links
from vfe_vision.domain.resolve_timeline import ResolveLink
from vfe_vision.domain.shots import MOTION_EN, MOTION_FR, shown_motion
from vfe_vision.domain.synthesis_input import Video as VideoFacts
from vfe_vision.domain.transcript import clean_untrusted
from vfe_vision.domain.translation import AS_WRITTEN, Dictionary
from vfe_vision.pipeline.synthesis_facts import load_facts
from vfe_vision.services import subjects, synthesis, videos
from vfe_vision.services.container import AppContainer
from vfe_vision.services.reading import texts_in
from vfe_vision.services.synthesis import SynthesisView
from vfe_vision.services.videos import VideoDetail

SUBJECT_LABELS = 8


@dataclass(frozen=True, slots=True)
class EditingData:
    detail: VideoDetail
    facts: VideoFacts
    synthesis: SynthesisView
    shots: list[Shot]
    stories: dict[str, list[ShotStory]]  # by shot id
    subject_labels: list[str] = field(default_factory=list)  # most often seen first
    resolve: list[ResolveLink] = field(default_factory=list)  # timelines using it
    texts: Dictionary = AS_WRITTEN  # the language its texts are read in

    @property
    def video(self) -> Video:
        return self.detail.video

    def shot_text(self, shot: Shot) -> str:
        """What the shot shows: its story (first part), else its first described keyframe."""
        told = self.stories.get(shot.id) or []
        if told:
            return clean_untrusted(self.texts(str(told[0].story.get("summary", ""))))
        for frame in self.facts.frames:
            if frame.shot == shot.idx and frame.data and frame.data.get("caption"):
                return clean_untrusted(self.texts(str(frame.data["caption"])))
        return ""

    @property
    def language(self) -> str:
        """The language of its texts and words (French when read as written)."""
        return self.texts.language or "fr"

    def shot_motion(self, shot: Shot) -> str:
        shown = shown_motion(shot.motion, shot.motion_score)
        return (MOTION_EN if self.language == "en" else MOTION_FR).get(shown, shown)

    def shot_roles(self, index: int) -> list[str]:
        """Editing roles suggested for a shot (establishing, b_roll, avoid), 0-based index."""
        roles = [s.role for s in self.synthesis.suggestions if index in s.shots]
        return list(dict.fromkeys(roles))

    def shot_usability(self, index: int) -> int | None:
        usable = self.synthesis.usability.get(index)
        return usable.score if usable is not None else None


def load_editing_data(
    c: AppContainer, video_id: str, *, tr: Dictionary | None = None
) -> EditingData:
    """``tr``: the language of the texts (default: the analysis language)."""
    tr = texts_in(c) if tr is None else tr
    detail = videos.get_video(c, video_id)  # NotFoundError first
    seen: Counter[str] = Counter()
    for frame in subjects.get_subjects(c, video_id, tr=tr).frames:
        if frame.duplicate_of is None:
            seen.update({clean_untrusted(s.label)[:40] for s in frame.subjects})
    labels = [label for label, _ in sorted(seen.items(), key=lambda kv: (-kv[1], kv[0])) if label]
    with c.db.read() as session:
        links = resolve_links(session, [detail.video.path_key]).get(detail.video.path_key, [])
    return EditingData(
        detail=detail,
        facts=load_facts(c.db, video_id),
        synthesis=synthesis.get_synthesis(c, video_id, tr=tr),
        shots=videos.get_shots(c, video_id),
        stories=videos.get_shot_stories(c, video_id),
        subject_labels=labels[:SUBJECT_LABELS],
        resolve=links,
        texts=tr,
    )
