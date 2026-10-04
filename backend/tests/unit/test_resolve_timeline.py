"""Resolve timelines as the application sees them: the files a timeline uses, where,
and the stored form of those links. Cases taken from a real project."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from vfe_vision.core.paths import is_video_file, path_key
from vfe_vision.domain.clip_paths import canonical_path
from vfe_vision.domain.resolve_timeline import (
    NAMES_KEPT,
    ClipKind,
    ClipUse,
    ResolveDatabase,
    ResolveProjectRef,
    TimelineClip,
    TimelineInfo,
    identity_parts,
    record_seconds,
    record_timecodes,
    resolve_identity,
    source_key,
    timeline_files,
    timeline_from_json,
    timeline_to_json,
    track_label,
    use_from_json,
    use_to_json,
)

TIMELINE = TimelineInfo(
    id="f50b89b8-f303-4025-88a4-105627347b2d",
    name="Timeline 1",
    fps=29.97,
    drop_frame=False,
    start_frame=108000,
    end_frame=249838,
    start_timecode="01:00:00:00",
    width=1920,
    height=1080,
    video_tracks=1,
    audio_tracks=1,
    video_clips=98,
    is_current=True,
)


def use(
    start: int,
    end: int,
    *,
    track_type: str = "video",
    track: int = 1,
    name: str = "clip.mp4",
    source: tuple[float, float] = (0.0, 1.0),
    name_suffix: int | None = None,
) -> ClipUse:
    if name_suffix is not None:
        name = f"{name} {name_suffix}"
    return ClipUse(
        track_type=track_type,
        track=track,
        track_name=f"{track_type.title()} {track}",
        timeline_item_id=f"item-{track_type}-{start}",
        media_pool_item_id=f"pool-{name}",
        name=name,
        enabled=True,
        record_start_frame=start,
        record_end_frame=end,
        source_start_s=source[0],
        source_end_s=source[1],
        source_start_frame=0,
        clip_fps=29.97,
    )


def clip(
    path: str | None,
    start: int,
    end: int,
    *,
    kind: str = ClipKind.FILE,
    clip_type: str | None = None,
    vfe_video_id: str | None = None,
    **kwargs: object,
) -> TimelineClip:
    name = Path(path).name if path else "Titre"
    return TimelineClip(
        file_path=path,
        clip_type=clip_type or ("Video + Audio" if path else None),
        use=use(start, end, name=name, **kwargs),  # type: ignore[arg-type]
        kind=kind,
        vfe_video_id=vfe_video_id,
    )


def files_of(clips: list[TimelineClip]):  # type: ignore[no-untyped-def]
    return timeline_files(
        clips, is_video=lambda p: is_video_file(Path(p)), canonical=canonical_path, key=path_key
    )


def test_one_entry_per_file_in_order_of_first_use() -> None:
    clips = [
        clip(r"D:\cats 2026\hdr\b.mp4", 108662, 110661),
        clip(r"D:\cats 2026\hdr\a.mp4", 108000, 108662),
        clip(r"D:\cats 2026\hdr\a.mp4", 110661, 111000),  # used twice
    ]
    files, skipped = files_of(clips)
    assert [Path(f.path).name for f in files] == ["a.mp4", "b.mp4"]
    assert [f.position for f in files] == [0, 1]
    assert len(files[0].uses) == 2
    assert skipped.graphics == 0
    assert skipped.not_video == 0


def test_linked_audio_does_not_repeat_the_video_clip() -> None:
    clips = [
        clip(r"D:\r\a.mp4", 108000, 108662),
        clip(r"D:\r\a.mp4", 108000, 108662, track_type="audio"),
    ]
    files, _ = files_of(clips)
    assert len(files) == 1
    assert [u.track_type for u in files[0].uses] == ["video"]


def test_a_video_used_for_its_sound_only_keeps_its_audio_uses() -> None:
    files, _ = files_of([clip(r"D:\r\interview.mov", 108000, 109000, track_type="audio", track=2)])
    assert [track_label(u) for u in files[0].uses] == ["A2"]


def test_paths_differing_in_case_or_slashes_are_one_file() -> None:
    files, _ = files_of(
        [
            clip(r"D:\Cats 2026\HDR\A.MP4", 108000, 108100),
            clip("d:/cats 2026/hdr/a.mp4", 108100, 108200),
        ]
    )
    assert len(files) == 1
    assert len(files[0].uses) == 2


def test_what_is_not_a_video_of_the_library_is_told_apart() -> None:
    clips = [
        clip(None, 108000, 108100),  # a title
        clip(None, 108100, 108200, kind=ClipKind.GRAPHICS),
        clip(None, 108200, 108300, kind=ClipKind.CONTAINER),  # compound clip
        clip(r"D:\music\song.wav", 108000, 109000, track_type="audio", clip_type="Audio"),
        clip(r"D:\stills\logo.png", 108100, 108200, clip_type="Still"),
        clip(r"D:\rush\A001.braw", 108300, 108400, clip_type="Video"),  # camera format
        clip("/Volumes/Rush/a.mov", 108400, 108500),  # a path of another computer
        clip(r"D:\r\a.mp4", 108500, 108600),
    ]
    files, skipped = files_of(clips)
    assert [Path(f.path).name for f in files] == ["a.mp4"]
    assert skipped.graphics == 2
    assert skipped.graphics_names == ("Titre",)
    assert skipped.containers == 1
    assert skipped.not_video == 2
    assert set(skipped.not_video_names) == {"song.wav", "logo.png"}
    assert skipped.unsupported == 1
    assert skipped.unsupported_names == ("A001.braw",)
    assert skipped.elsewhere == 1
    assert skipped.elsewhere_names == ("/Volumes/Rush/a.mov",)


def test_skipped_names_are_capped() -> None:
    clips = [clip(None, 108000 + i, 108001 + i, name_suffix=i) for i in range(NAMES_KEPT + 5)]
    _, skipped = files_of(clips)
    assert skipped.graphics == NAMES_KEPT + 5  # every item counts
    assert len(skipped.graphics_names) <= NAMES_KEPT


def test_long_path_prefix_and_network_shares() -> None:
    files, _ = files_of(
        [
            clip("\\\\?\\D:\\cats 2026\\hdr\\a.mp4", 108000, 108100),
            clip(r"D:\cats 2026\hdr\a.mp4", 108100, 108200),
            clip("\\\\?\\UNC\\nas\\share\\b.mov", 108200, 108300),
        ]
    )
    assert [f.path for f in files] == [r"D:\cats 2026\hdr\a.mp4", "\\\\nas\\share\\b.mov"]
    assert len(files[0].uses) == 2


def test_our_video_id_written_in_resolve_travels_with_the_file() -> None:
    files, _ = files_of([clip(r"D:\r\a.mp4", 108000, 108100, vfe_video_id="01abc")])
    assert files[0].vfe_video_id == "01abc"


def test_a_file_used_only_on_disabled_clips_or_tracks() -> None:
    off = ClipUse(**{**use_to_json(use(108000, 108100)), "enabled": False})
    muted = ClipUse(**{**use_to_json(use(108100, 108200)), "track_enabled": False})
    files, _ = files_of(
        [
            TimelineClip(file_path=r"D:\r\a.mp4", clip_type="Video", use=off),
            TimelineClip(file_path=r"D:\r\b.mp4", clip_type="Video", use=muted),
            clip(r"D:\r\c.mp4", 108200, 108300),
        ]
    )
    assert [f.enabled for f in files] == [False, False, True]


def test_record_positions_are_relative_to_the_timeline_start() -> None:
    first = use(108000, 108662)
    assert record_seconds(first, TIMELINE) == (0.0, 662 / 29.97)
    assert record_timecodes(first, TIMELINE) == ("01:00:00:00", "01:00:22:02")


def test_drop_frame_timelines_show_drop_frame_timecodes() -> None:
    timeline = TimelineInfo(
        id="t", name="DF", fps=29.97, drop_frame=True, start_frame=107892,
        end_frame=200000, start_timecode="01:00:00;00",
    )  # fmt: skip
    tcs = record_timecodes(use(107892 + 1800, 107892 + 1801), timeline)
    assert tcs is not None
    assert tcs[0] == "01:01:00;02"  # frames 00 and 01 are skipped at each minute
    # The same start timecode written with a colon is still read as drop-frame.
    colon = record_timecodes(use(107892, 107893), replace(timeline, start_timecode="01:00:00:00"))
    assert colon is not None
    assert colon[0] == "01:00:00;00"


def test_an_unreadable_start_timecode_gives_no_timecode() -> None:
    timeline = TimelineInfo(
        id="t", name="x", fps=25.0, drop_frame=False, start_frame=0, end_frame=10,
        start_timecode="n/a",
    )  # fmt: skip
    assert record_timecodes(use(0, 5), timeline) is None


def test_uses_and_timelines_round_trip_through_json() -> None:
    original = use(108000, 108662, name="20260607_191341.mp4", source=(0.0, 22.0887333))
    assert use_from_json(use_to_json(original)) == ClipUse(
        **{**use_to_json(original), "source_end_s": 22.0887}  # stored to 1/10,000 s
    )
    stored = timeline_to_json(TIMELINE)
    back = timeline_from_json(stored)
    assert back.name == TIMELINE.name
    assert back.fps == TIMELINE.fps
    assert back.start_frame == TIMELINE.start_frame
    assert not back.is_current  # whether it is Resolve's current timeline is not stored


def test_stored_uses_tolerate_missing_and_odd_values() -> None:
    back = use_from_json({"record_start_frame": "108000", "clip_fps": "n/a", "enabled": 0})
    assert back.record_start_frame == 108000
    assert back.clip_fps is None
    assert back.enabled is False
    assert back.track_type == "video"
    assert back.track == 1


def test_identity_round_trip() -> None:
    database = ResolveDatabase(type="Disk", name="Local Database")
    project = ResolveProjectRef(id="7c52df83", name="cats 2026")
    stored = resolve_identity("DaVinci Resolve Studio", "21.1.0.17", database, project, TIMELINE)
    assert stored["schema_version"] == 1
    db2, project2, timeline2 = identity_parts(stored)
    assert (db2, project2, timeline2.id) == (database, project, TIMELINE.id)
    assert source_key(project.id, TIMELINE.id) == f"7c52df83/{TIMELINE.id}"


@given(
    st.lists(
        st.tuples(
            st.sampled_from(["a.mp4", "b.MOV", "c.mxf", "song.wav", ""]),
            st.integers(min_value=0, max_value=10_000),
            st.sampled_from(["video", "audio"]),
        ),
        max_size=40,
    )
)
def test_every_video_file_is_listed_once(items: list[tuple[str, int, str]]) -> None:
    clips = [
        clip(rf"D:\r\{name}" if name else None, start, start + 10, track_type=kind)
        for name, start, kind in items
    ]
    files, skipped = files_of(clips)
    keys = [path_key(f.path) for f in files]
    assert len(keys) == len(set(keys))
    videos = {path_key(rf"D:\r\{n}") for n, _, _ in items if n and n != "song.wav"}
    assert set(keys) == videos
    assert all(f.uses for f in files)
    assert skipped.graphics == sum(1 for n, _, _ in items if not n)
    assert [f.position for f in files] == list(range(len(files)))
