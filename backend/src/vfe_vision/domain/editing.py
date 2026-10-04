"""Editing suggestions (pure functions): the blocks to open on, to cover with and to avoid,
the highlight moments, and where to cut each clip in and out.

Code decides all of it, from measurements and the vision model's per-frame flags; the language
model only says why a chosen block is worth using. Nothing says what the « best moment » truly
is (no ground truth): the UI calls these suggestions and shows the criteria, and the rules are
versioned so that a change recomputes them.

A clip's picture stays inside its block, hence inside its shot. When speech runs across a picture
edge, the sound gets a range of its own, cut in the best pause between two words up to 3 s beyond
the picture (an L-cut or a J-cut): letting the picture spill to finish the sentence instead moved
63 of 221 edges into the next shot.
"""

from __future__ import annotations

import bisect
import itertools
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, NamedTuple

from vfe_vision.domain.synthesis_input import (
    Block,
    Box,
    Frame,
    Picture,
    Video,
    Word,
    whole_words,
)
from vfe_vision.domain.vision import EditingValue, QualityIssue, ShotType

EDITING_RULES_VERSION = 1  # stored with the synthesis: a new rule recomputes the suggestions

USABLE = 50  # usability below this: a block to avoid
B_ROLL_USABLE = 70
MIN_ROLE_S = 1.5  # a shorter block gets no role and is no highlight
B_ROLL_MIN_S = 2.0
MAX_ESTABLISHING = 3  # the first ones: an opening shot comes early
HIGHLIGHT_EVERY_S = 60.0  # one highlight per minute…
MAX_HIGHLIGHTS = 8
HIGHLIGHT_SHARE = 3  # …and at most one usable block in three (RIZ: 2 of 8 where 5 was too many)
SHORT_HIGHLIGHT_S = 2.0  # a shorter highlight is hard to use
SHORT_PENALTY = 30.0
CONSENSUS_FRAMES = 3  # this many keyframes described alike: the description is reliable

CLIP_S = 6.0
TALK_CLIP_S = 10.0  # when speech covers more than TALK_SHARE of the block: room for a sentence
TALK_SHARE = 0.3
B_ROLL_CLIP_S = 8.0
EDGE_SNAP_S = 0.5  # a clip edge this close to its block's goes to it (no flash of a neighbour)
MIN_CLIP_S = 2.0
SOUND_REACH_S = 3.0  # how far the sound may run beyond the picture to reach a pause
WORD_PAD_S = 0.08  # an edge closer than this to a word would clip it
SHIFT_COST = 0.25  # per second an edge moves to reach a pause
MAX_PAUSE_S = 2.0  # a longer pause is no better a place to cut
SENTENCE_END_BONUS = 0.5
# A pause the audio levels measured as a silence is surely quiet: stored silences already held
# 12 of the 25 snapped edges of the talk and 5 of 19 of the recipe. Within SOUND_REACH_S of
# the edge only, as it was measured.
SILENCE_BONUS = 0.5
MIN_SILENCE_S = 0.05  # of overlap between a pause and a silence
SPEECH_EDGE_S = 0.15  # the cut before the first word, after the last one
SAME_S = 0.05  # a sound edge this close to the picture's is the picture's

_SIMILAR = 0.5  # Jaccard of the words: two captions of one group
_WORD = re.compile(r"[\wÀ-ÿ']+")
_STOP = frozenset({
    "un", "une", "des", "de", "du", "la", "le", "les", "l", "d", "et", "à", "au", "aux", "en",
    "sur", "dans", "avec", "a", "the", "of", "in", "on", "and", "with",
})  # fmt: skip
_ENGLISH = frozenset(
    {"the", "of", "and", "with", "is", "are", "in", "on", "an", "its", "from", "against", "under"}
)
_ENGLISH_WORD = re.compile(r"[a-z']+")
_SENTENCE_END = re.compile(r"[.!?…]\s*$")
_WIDE = frozenset({ShotType.WIDE, ShotType.EXTREME_WIDE})
_WIDE_VOTE = "@wide"  # a vote of its own next to the editing values
_EDGE_S = 1e-6  # float noise: a keyframe stored at a block's first image belongs to it


@dataclass(frozen=True, slots=True)
class Roles:
    """Block numbers suggested for each editing role, in time order."""

    establishing: tuple[int, ...]
    b_roll: tuple[int, ...]
    avoid: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Clip:
    """Where to cut a clip, in seconds of the source file. The sound range is set only when it
    differs from the picture's (an L-cut or a J-cut)."""

    picture_in: float
    picture_out: float
    sound_in: float | None
    sound_out: float | None
    notes: tuple[str, ...]  # French, for the editor


class _Pause(NamedTuple):
    """A place between two words where a cut splits none."""

    t: float
    quality: float  # the gap (up to 2 s), +0.5 after a sentence end
    silent: bool  # in a silence the audio levels measured: surely quiet


# ---------------------------------------------------------------- roles
def roles(blocks: Sequence[Block], usable: Mapping[int, int]) -> Roles:
    """Opening shots (wide, or flagged establishing, the first three), B-roll (illustration or
    detail, nobody talking to the camera, clean, 2 s or more) and the blocks to avoid (usability
    under 50), from the majority of each block's described keyframes.

    Speech under a B-roll block does not matter: its sound is not used. A block without a score
    gets no role.
    """
    establishing: list[int] = []
    b_roll: list[int] = []
    avoid: list[int] = []
    for block in blocks:
        score = usable.get(block.no)
        if score is None:
            continue
        if score < USABLE:
            avoid.append(block.no)
            continue
        if block.duration < MIN_ROLE_S:
            continue
        votes, voters = _votes(block)
        half = voters / 2  # counts: six summed twelfths make 0.49999999999999994
        if votes[_WIDE_VOTE] >= half or votes[EditingValue.ESTABLISHING] >= half:
            establishing.append(block.no)
        if (
            votes[EditingValue.B_ROLL] + votes[EditingValue.DETAIL] >= half
            and votes[EditingValue.INTERVIEW] < half
            and score >= B_ROLL_USABLE
            and block.duration >= B_ROLL_MIN_S
        ):
            b_roll.append(block.no)
    return Roles(tuple(establishing[:MAX_ESTABLISHING]), tuple(b_roll), tuple(avoid))


def _votes(block: Block) -> tuple[Counter[str], int]:
    """How many described keyframes give each editing value (and are wide), and out of how many
    (at least 1: a block without a description has no majority)."""
    described = [frame.data for frame in block.frames if frame.data]
    votes: Counter[str] = Counter()
    for data in described:
        votes.update(_values(data))
        if data.get("shot_type") in _WIDE:
            votes[_WIDE_VOTE] += 1
    return votes, len(described) or 1


# ---------------------------------------------------------------- highlights
def salience(block: Block, usable: int) -> float:
    """How strong a highlight the block makes, from criteria code can show:
    50 × usability/100 + 20 × share of hero keyframes + 15 × share of keyframes with an action
    + 10 × agreement of the descriptions (3 alike = full) + 5 × speech share, − 30 under 2 s."""
    described = [frame.data for frame in block.frames if frame.data]
    count = len(described) or 1
    hero = sum(EditingValue.HERO in _values(d) for d in described) / count
    action = sum(EditingValue.ACTION in _values(d) or bool(d.get("actions")) for d in described)
    consensus = min(1.0, max(_caption_groups(block.frames), default=0) / CONSENSUS_FRAMES)
    talk = min(1.0, block.speech_s / max(1.0, block.duration))
    short = SHORT_PENALTY if block.duration < SHORT_HIGHLIGHT_S else 0.0
    return 50 * usable / 100 + 20 * hero + 15 * action / count + 10 * consensus + 5 * talk - short


def highlight_count(duration: float, usable_blocks: int) -> int:
    """How many highlights a video gets: one a minute (1 to 8), and at most a third of its
    usable blocks, at least one when it has any (RIZ 2:18 → 2, the 4:23 talk → 4, 8 min → 8).

    A product default, not a measurement. Half a minute rounds up (2:30 → 3); an unknown
    (non-finite) duration counts as none.
    """
    if usable_blocks <= 0:
        return 0
    minutes = duration / HIGHLIGHT_EVERY_S if math.isfinite(duration) else 0.0
    per_minute = min(MAX_HIGHLIGHTS, max(1, math.floor(minutes + 0.5)))
    return max(1, min(per_minute, usable_blocks // HIGHLIGHT_SHARE))


def allowed_blocks(blocks: Sequence[Block], usable: Mapping[int, int]) -> list[int]:
    """The blocks a highlight may come from: usable (50 or more) and 1.5 s or longer; every block
    when none is."""
    allowed = [b.no for b in blocks if usable.get(b.no, 0) >= USABLE and b.duration >= MIN_ROLE_S]
    return allowed or [b.no for b in blocks]


def pick_highlights(
    blocks: Sequence[Block],
    chapters: Sequence[tuple[int, int]],
    usable: Mapping[int, int],
    count: int,
) -> dict[int, list[int]]:
    """The highlight blocks of each chapter (by chapter index from 0), best first.

    ``chapters`` are (first, last) block numbers. The best block of every chapter comes first,
    then the second bests, and so on up to ``count``: the moments spread over the whole video
    instead of gathering where the model's attention goes (82 % of its first picks were a
    chapter's first or last block). Within a chapter a tie goes to the longer block; when a
    round has more candidates than places left, the most salient win (the earlier chapter on a
    tie), so that the last chapters of a long video are not left out for coming last.
    """
    allowed = set(allowed_blocks(blocks, usable))
    score = {b.no: salience(b, usable.get(b.no, 0)) for b in blocks if b.no in allowed}
    ranked = [
        [
            b.no
            for b in sorted(
                (b for b in blocks if first <= b.no <= last and b.no in allowed),
                key=lambda b: (-score[b.no], -b.duration),
            )
        ]
        for first, last in chapters
    ]
    picks: dict[int, list[int]] = {index: [] for index in range(len(chapters))}
    left = max(0, count)
    for rank in range(max(map(len, ranked), default=0)):
        if not left:
            break
        candidates = [(index, own[rank]) for index, own in enumerate(ranked) if rank < len(own)]
        if len(candidates) > left:
            kept = set(sorted(candidates, key=lambda c: -score[c[1]])[:left])  # stable: ties
            candidates = [c for c in candidates if c in kept]
        for index, no in candidates:
            picks[index].append(no)
        left -= len(candidates)
    return picks


def _caption_groups(frames: Sequence[Frame]) -> list[int]:
    """The sizes of the groups of near-identical captions (``distinct_captions``): a caption
    joins the first group whose shown caption shares half of its words; a group shows its first
    caption in the output language rather than in English (the next ones are compared with it)."""
    groups: list[tuple[str, int]] = []
    for frame in frames:
        if not frame.data:
            continue
        caption = str(frame.data.get("caption") or "").strip()
        for index, (shown, size) in enumerate(groups):
            if _similar(shown, caption):
                english = _looks_english(shown) and not _looks_english(caption)
                groups[index] = (caption if english else shown, size + 1)
                break
        else:
            groups.append((caption, 1))
    return [size for _, size in groups]


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2}


def _similar(a: str, b: str) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= _SIMILAR


def _looks_english(text: str) -> bool:
    return sum(w in _ENGLISH for w in _ENGLISH_WORD.findall(text.lower())) >= 2


def _values(data: Mapping[str, Any]) -> set[str]:
    """The keyframe's editing values (hero, b_roll…)."""
    value = data.get("editing_value")
    return {str(v) for v in value} if isinstance(value, list) else set()


# ---------------------------------------------------------------- where in the block
# Which part of a block a clip shows. Centring it on the sharpest keyframe picked the worst
# seconds of phone rushes: sharpness measures fine texture, not interest, so leaves filling the
# frame once the plane had flown past, or the ground as the phone was lowered at the end of the
# recording, won (an audit of 60 highlights: in 26 of 47 blocks with a choice, most of
# the block was calmer than the clip; 24 clips held a flagged keyframe). Every half second of the
# block is now scored from what code can show — a subject on screen (a living being, or what
# the vision model names that is no scenery, a living being ahead of a thing), more of them,
# bigger, not leaving the frame, no jolt of the camera, no black or burnt picture, no defect
# flag (blur costs half), not the end of the recording — and the clip takes the best-scored
# stretch. Sharpness is left out altogether: it measures texture, so a sky with birds read as
# blurred and leaves as sharp.
STEP_S = 0.5
SUBJECT_WEIGHT = 0.55
CALM_WEIGHT = 0.45
HERO_BONUS = 0.10  # the vision model calls the nearest keyframe a hero or action frame
# Camera motion above the block's usual (its median, at least CALM_SCALE) halves calmness when it
# is that much again: a handheld macro that follows bees moves all along, only its jolts count.
CALM_SCALE = 1.0  # analysis pass units (0 on a tripod, ~0.3 handheld, 5–10 lowering the phone)
DARK_LUMA = 0.04
BRIGHT_LUMA = 0.97
BAD_LIGHT_FACTOR = 0.2  # a black or burnt picture
TAIL_S = 1.0  # the end of the recording: the phone being lowered
TAIL_FACTOR = 0.5
PRESENT = 0.7  # a living being the detectors boxed (a bee, a cat, a person)…
MORE_OF_THEM = 0.15  # …+ this for each more one (four birds), up to 1
THING = 0.55  # a main subject named by the vision model that is neither scenery nor a being
SCENERY_ONLY = 0.35  # described, and nothing but scenery (sky, roofs, leaves) on screen
UNDESCRIBED = 0.5  # no description: neutral
FULL_SIDE = 0.4  # a box side this share of the picture (square root of its area) counts as full
SMALL_BOX_WEIGHT = 0.25  # how much a small box lowers the score
LEAVING_AREA = 0.25  # a box under this area touching an edge: the subject is leaving the frame
EDGE_BOX = 0.02
LEAVING_FACTOR = 0.6
# A defect flag of the vision model at the nearest keyframe: what the score is multiplied by.
DEFECT_FACTORS: dict[str, float] = {
    QualityIssue.BLUR: 0.5, QualityIssue.MOTION_BLUR: 0.5,
    QualityIssue.OUT_OF_FOCUS_SUBJECT: 0.5, QualityIssue.OBSTRUCTION: 0.5,
    QualityIssue.OVEREXPOSED: 0.7, QualityIssue.UNDEREXPOSED: 0.7,
}  # fmt: skip
# What a main-subject label names when it is only the setting: its head word (first in French,
# last in English), singular. The birds video named the roof main at 6 keyframes of 8.
SCENERY = frozenset({
    "ciel", "nuage", "soleil", "horizon", "lumière", "ombre", "arbre", "arbuste", "feuille",
    "feuillage", "branche", "herbe", "pelouse", "gazon", "plante", "végétation", "buisson",
    "haie", "forêt", "bois", "fleur", "sol", "terre", "sable", "rocher", "roche", "pierre",
    "gravier", "chemin", "sentier", "route", "rue", "trottoir", "pavé", "mur", "muret",
    "façade", "bâtiment", "immeuble", "maison", "toit", "toiture", "fenêtre", "porte",
    "clôture", "grille", "balcon", "terrasse", "jardin", "parc", "paysage", "montagne",
    "colline", "vallée", "falaise", "lac", "mer", "océan", "eau", "rivière", "fleuve", "plage",
    "vague", "côte", "rive", "berge", "neige", "glace", "ville", "village", "panorama", "décor",
    "fond", "arrière-plan", "plafond", "pièce",
    "sky", "cloud", "sun", "tree", "bush", "leaf", "leaves", "foliage", "branch", "grass",
    "plant", "flower", "ground", "road", "street", "path", "wall", "building", "house", "roof",
    "window", "door", "fence", "garden", "park", "landscape", "mountain", "hill", "lake",
    "sea", "ocean", "water", "river", "beach", "wave", "shore", "snow", "city", "town",
    "background", "ceiling", "room",
})  # fmt: skip
# Parts of the person filming or of someone off frame: no subject of their own.
BODY_PARTS = frozenset({
    "main", "pied", "jambe", "bras", "doigt", "genou", "hand", "foot", "feet", "leg", "arm",
    "finger", "knee",
})  # fmt: skip


def _main_labels(frame: Frame) -> list[str]:
    subjects = (frame.data or {}).get("subjects") or []
    return [
        str(s.get("label") or "")
        for s in subjects
        if isinstance(s, dict) and s.get("is_main") and s.get("label")
    ]


def _singular(word: str) -> str:
    """« oiseaux » → « oiseau », « feuilles » → « feuille » (enough to compare labels)."""
    if len(word) > 4 and word.endswith("x"):
        return word[:-1]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def is_scenery(label: str) -> bool:
    """The label names the setting or a body part (its first or last word), not a subject."""
    words = [w for w in _WORD.findall(label.lower()) if w not in _STOP]
    ends = set(words[:1] + words[-1:])
    return any(word in SCENERY or word in BODY_PARTS for w in ends for word in (w, _singular(w)))


def subject_score(frame: Frame, picture: Picture | None) -> float:
    """How well the keyframe shows a subject (0–1): living beings the detectors boxed (how many,
    how big, whether leaving the frame), else things the vision model named as main subjects
    that are no scenery (a plane, lavender), else scenery only."""
    if not frame.data:
        return UNDESCRIBED
    beings = picture.beings.get(frame.keyframe_id, ()) if picture else ()
    named = [label for label in _main_labels(frame) if not is_scenery(label)]
    score = min(1.0, THING + MORE_OF_THEM * (len(named) - 1)) if named else SCENERY_ONLY
    if beings:
        present = min(1.0, PRESENT + MORE_OF_THEM * (len(beings) - 1))
        area = max((x2 - x1) * (y2 - y1) for x1, y1, x2, y2 in beings)
        size = min(1.0, math.sqrt(max(0.0, area)) / FULL_SIDE)
        present *= 1 - SMALL_BOX_WEIGHT * (1 - size)
        if all(_leaving(box) for box in beings):
            present *= LEAVING_FACTOR
        score = max(score, present)
    return score


def _defect_factor(data: Mapping[str, Any]) -> float:
    flags = data.get("quality_issues") or []
    return min((DEFECT_FACTORS.get(str(q), 1.0) for q in flags), default=1.0)


def _leaving(box: Box) -> bool:
    x1, y1, x2, y2 = box
    touches = min(x1, y1) < EDGE_BOX or max(x2, y2) > 1 - EDGE_BOX
    return touches and (x2 - x1) * (y2 - y1) < LEAVING_AREA


def moment_scores(video: Video, block: Block, picture: Picture | None) -> list[tuple[float, float]]:
    """(t, score 0–1.1) every STEP_S of the block: how good a picture it is at that instant."""
    frames = sorted(block.frames, key=lambda f: f.t)
    subject = [(f.t, subject_score(f, picture)) for f in frames]
    moving = sorted(m for t in _steps(block) if (m := _sample(picture, "motion", t)) is not None)
    usual = moving[len(moving) // 2] if moving else 0.0
    scale = max(CALM_SCALE, usual)
    ends_recording = block.end >= video.duration - STEP_S
    scores: list[tuple[float, float]] = []
    for t in _steps(block):
        nearest = min(frames, key=lambda f: abs(f.t - t), default=None)
        data = (nearest.data or {}) if nearest else {}
        motion = _sample(picture, "motion", t)
        calm = 1.0 if motion is None else 1 / (1 + max(0.0, motion - usual) / scale)
        score = SUBJECT_WEIGHT * _interpolate(subject, t) + CALM_WEIGHT * calm
        luma = _sample(picture, "luma", t)
        if luma is not None and not DARK_LUMA < luma < BRIGHT_LUMA:
            score *= BAD_LIGHT_FACTOR
        score *= _defect_factor(data)
        if ends_recording and t >= video.duration - TAIL_S:
            score *= TAIL_FACTOR
        if _values(data) & {EditingValue.HERO, EditingValue.ACTION}:
            score += HERO_BONUS
        scores.append((t, score))
    return scores


def _steps(block: Block) -> list[float]:
    count = max(1, math.ceil((block.end - block.start) / STEP_S - 1e-9))
    return [block.start + i * STEP_S for i in range(count)]


def best_window(
    video: Video, block: Block, picture: Picture | None, target: float
) -> tuple[float, float]:
    """The ``target`` seconds of the block with the best mean moment score (the earliest on a
    tie), the whole block when shorter; an edge within EDGE_SNAP_S of the block's goes to it."""
    lo, hi = block.start, block.end
    if hi - lo <= target:
        return lo, hi
    scores = moment_scores(video, block, picture)
    starts = [lo + i * STEP_S for i in range(int((hi - target - lo) / STEP_S + 1e-9) + 1)]
    if starts[-1] < hi - target - 1e-9:
        starts.append(hi - target)

    def mean(start: float) -> float:
        inside = [s for t, s in scores if start - 1e-9 <= t < start + target - 1e-9]
        return sum(inside) / len(inside) if inside else 0.0

    start = max(starts, key=lambda s: (round(mean(s), 6), -s))
    end = start + target
    if start - lo < EDGE_SNAP_S:
        start = lo
    if hi - end < EDGE_SNAP_S:
        end = hi
    return start, end


def _sample(picture: Picture | None, key: str, t: float) -> float | None:
    """The signal's value at the sample nearest ``t`` (half a step away at most)."""
    if picture is None or not picture.t:
        return None
    values: tuple[float | None, ...] = getattr(picture, key)
    i = bisect.bisect_left(picture.t, t)
    near = [j for j in (i - 1, i) if 0 <= j < min(len(picture.t), len(values))]
    if not near:
        return None
    j = min(near, key=lambda k: abs(picture.t[k] - t))
    return values[j] if abs(picture.t[j] - t) <= 1 / picture.hz else None


def _interpolate(points: Sequence[tuple[float, float]], t: float) -> float:
    """Linear between the keyframes around ``t``, the nearest one's beyond them."""
    if not points:
        return UNDESCRIBED
    if t <= points[0][0]:
        return points[0][1]
    for (t0, v0), (t1, v1) in itertools.pairwise(points):
        if t0 <= t <= t1:
            return v0 if t1 == t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return points[-1][1]


# ---------------------------------------------------------------- in / out
def best_frame(
    block: Block, picture: Picture | None = None, within: tuple[float, float] | None = None
) -> Frame | None:
    """The keyframe that shows the clip. With the picture facts: the one inside ``within`` (the
    clip) that shows its subjects best, with its defect flags, sharpness only
    breaking ties. Without them: the sharpest, worth half with a defect flag and half
    as much again when the vision model calls it a hero or action frame."""
    inside = [f for f in block.frames if block.start - _EDGE_S <= f.t < block.end]
    if picture is None:
        return max(inside or block.frames, key=_frame_quality, default=None)
    lo, hi = within or (block.start, block.end)
    candidates = [f for f in inside if lo - _EDGE_S <= f.t < hi] or inside or list(block.frames)

    def key(frame: Frame) -> tuple[float, float]:
        score = subject_score(frame, picture) * _defect_factor(frame.data or {})
        return round(score, 6), frame.sharpness or 0.0

    return max(candidates, key=key, default=None)


def _frame_quality(frame: Frame) -> float:
    data = frame.data or {}
    flagged = 0.5 if data.get("quality_issues") else 1.0
    strong = 1.5 if _values(data) & {EditingValue.HERO, EditingValue.ACTION} else 1.0
    return (frame.sharpness or 0.0) * flagged * strong


def clip_for(
    video: Video, block: Block, *, broll: bool = False, picture: Picture | None = None
) -> Clip:
    """Where to cut a clip of the block: its best 6 s (10 s when speech covers more than 30 % of
    it, 8 s for B-roll), the whole block when shorter, snapped to its edges. With the picture
    facts, the best-scored stretch (``best_window``); without them, around its best keyframe.

    An edge that would clip a word moves to the best pause: up to 3 s
    inward, outward up to 3 s beyond the block. Inside the block the picture moves with the
    sound; beyond it, the picture stops at the block's edge and the sound alone goes on to the
    pause (L-cut) or starts early (J-cut). B-roll ignores speech: nobody uses its sound.
    """
    lo, hi = block.start, block.end
    talky = block.speech_s / max(0.1, block.duration) > TALK_SHARE
    target = B_ROLL_CLIP_S if broll else TALK_CLIP_S if talky else CLIP_S
    if picture is not None:
        start, end = best_window(video, block, picture, target)
    else:
        frame = best_frame(block)
        start, end = _window(lo, hi, frame.t if frame else lo, target)
    return cut_edges(video, lo, hi, start, end, broll=broll)


def cut_edges(
    video: Video, lo: float, hi: float, start: float, end: float, *, broll: bool = False
) -> Clip:
    """Where to cut [start, end] so that no word is clipped (also used for any range
    an editor asks about): the picture stays in [lo, hi] (its shot or shots), an edge inside
    a word moves to the best pause, and beyond [lo, hi] only the sound goes on (L-cut) or starts
    early (J-cut), up to 3 s. ``broll``: speech is ignored."""
    reach = SOUND_REACH_S + 1.0
    words, pauses = ([], []) if broll else _speech_near(video, lo - reach, hi + reach)
    if not words:
        return Clip(start, end, None, None, ())

    # The picture keeps MIN_CLIP_S when the block allows it, the whole block when it does not;
    # the sound keeps within SOUND_REACH_S of the block and lasts MIN_CLIP_S too (the in
    # point plus 2 s, not the picture's, bounds the out point).
    latest_in = min(start + SOUND_REACH_S, max(lo, hi - MIN_CLIP_S))
    sound_in = _snap(start, max(0.0, lo - SOUND_REACH_S), latest_in, words, pauses)
    if lo - SAME_S < sound_in < lo and not _in_word(words, lo, 0.0):
        sound_in = lo
    picture_in = min(max(sound_in, lo), hi)
    last = min(max(video.duration, hi), hi + SOUND_REACH_S)
    earliest_out = max(sound_in + MIN_CLIP_S, min(picture_in + MIN_CLIP_S, hi), end - SOUND_REACH_S)
    sound_out = _snap(end, earliest_out, last, words, pauses)
    if hi < sound_out < hi + SAME_S and not _in_word(words, hi, 0.0):
        sound_out = hi
    picture_out = min(max(sound_out, picture_in), hi)

    notes: list[str] = []
    if sound_in < picture_in:
        notes.append(f"le son commence {_seconds(picture_in - sound_in)} avant l'image (J-cut)")
    elif sound_in != start:  # not when a pause 50 ms away went back to the block's edge
        notes.append("entrée déplacée entre deux mots")
    if sound_out > picture_out:
        notes.append(f"le son continue {_seconds(sound_out - picture_out)} après la coupe (L-cut)")
    elif sound_out != end:
        notes.append("sortie déplacée entre deux mots")
    if _in_word(words, sound_in, 0.0) or _in_word(words, sound_out, 0.0):
        notes.append("parole continue : coupe dans un mot inévitable")
    # Unrounded: a cut between two words that touch is exactly where one ends.
    if sound_in == picture_in and sound_out == picture_out:
        return Clip(picture_in, picture_out, None, None, tuple(notes))
    return Clip(picture_in, picture_out, sound_in, sound_out, tuple(notes))


def _window(lo: float, hi: float, centre: float, target: float) -> tuple[float, float]:
    """``target`` seconds of [lo, hi] around ``centre`` (a third before it), the whole of it when
    shorter; an edge within EDGE_SNAP_S of the block's goes to it."""
    if hi - lo <= target:
        return lo, hi
    start = min(max(lo, centre - target / 3), hi - target)
    end = start + target
    if start - lo < EDGE_SNAP_S:
        start = lo
    if hi - end < EDGE_SNAP_S:
        end = hi
    return start, end


def _speech_near(video: Video, low: float, high: float) -> tuple[list[Word], list[_Pause]]:
    """The whole words said around [low, high] and the pauses between them.

    The segments reaching the range come with one more on each side, so that the pause before
    the first word and after the last one is measured against the words around it; a whole
    video's words would cost every clip of a one-hour talk a pass over thousands of them.
    """
    segments = sorted(video.segments, key=lambda s: (s.start, s.end))
    near = [i for i, s in enumerate(segments) if s.end >= low and s.start <= high]
    if not near:
        return [], []
    first, last = max(0, near[0] - 1), min(len(segments) - 1, near[-1] + 1)
    words = sorted(
        (w for s in segments[first : last + 1] for w in whole_words(s)),
        key=lambda w: (w.start, w.end),
    )
    reach = (words[0].start - 1.0, words[-1].end + 1.0)
    silences = [s for s in video.silences if s[1] >= reach[0] and s[0] <= reach[1]]
    pauses = _pauses(words, silences, before=first == 0, after=last == len(segments) - 1)
    return words, pauses


def _pauses(
    words: Sequence[Word],
    silences: Sequence[tuple[float, float]],
    *,
    before: bool,
    after: bool,
) -> list[_Pause]:
    """Where a cut splits no word, and how good a place it is: the middle of each gap between
    two words, better the longer the gap (up to 2 s) and after a sentence end; the middle of the
    measured silence the gap meets, if any. ``before``/``after``: the words start/end the speech
    of the video (a cut just before/after them is as good as it gets)."""
    pauses: list[_Pause] = []
    if before:
        at = max(0.0, words[0].start - SPEECH_EDGE_S)
        pauses.append(_Pause(at, MAX_PAUSE_S, _in_silence(at, silences)))
    for word, following in itertools.pairwise(words):
        gap = following.start - word.end
        if gap < 0:
            continue
        quality = min(gap, MAX_PAUSE_S)
        if _SENTENCE_END.search(word.text):
            quality += SENTENCE_END_BONUS
        quiet = _silent_part(word.end, following.start, silences)
        if quiet is None:
            pauses.append(_Pause((word.end + following.start) / 2, quality, silent=False))
        else:
            pauses.append(_Pause((quiet[0] + quiet[1]) / 2, quality, silent=True))
    if after:
        at = words[-1].end + SPEECH_EDGE_S
        pauses.append(_Pause(at, MAX_PAUSE_S, _in_silence(at, silences)))
    return pauses


def _silent_part(
    start: float, end: float, silences: Sequence[tuple[float, float]]
) -> tuple[float, float] | None:
    """The longest part of [start, end] inside a measured silence (MIN_SILENCE_S or more)."""
    parts = [(max(start, a), min(end, b)) for a, b in silences]
    best = max(parts, key=lambda p: p[1] - p[0], default=None)
    return best if best is not None and best[1] - best[0] >= MIN_SILENCE_S else None


def _in_silence(t: float, silences: Sequence[tuple[float, float]]) -> bool:
    return any(a <= t <= b for a, b in silences)


def _snap(
    t: float, low: float, high: float, words: Sequence[Word], pauses: Sequence[_Pause]
) -> float:
    """``t`` when it clips no word; else the best pause in [low, high] (``t`` when there is
    none), a pause losing SHIFT_COST per second away from ``t``. A measured silence counts
    within SOUND_REACH_S only, as measured: further, its bonus pulled a talk's 10 s clip
    7.5 s back to a silence, where a pause 1 s away served."""
    if not _in_word(words, t, WORD_PAD_S):
        return t
    scored = [
        (
            pause.quality
            + (SILENCE_BONUS if pause.silent and abs(pause.t - t) <= SOUND_REACH_S else 0.0)
            - SHIFT_COST * abs(pause.t - t),
            pause.t,
        )
        for pause in pauses
        if low <= pause.t <= high
    ]
    return max(scored)[1] if scored else t


def _in_word(words: Sequence[Word], t: float, pad: float) -> bool:
    return any(w.start - pad < t < w.end + pad for w in words)


def _seconds(value: float) -> str:
    """« 1,4 s »; « 0,06 s » under a tenth (never « 0,0 s »)."""
    digits = 1 if value >= 0.095 else 2
    return f"{value:.{digits}f} s".replace(".", ",")
