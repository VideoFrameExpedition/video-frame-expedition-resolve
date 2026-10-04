"""The fixed Resolve script: run against a fake Resolve object model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.fakes.resolve import FakeClip, FakeFolder, FakeProject, FakeResolve, apply, run_script
from vfe_vision.adapters.resolve.script import (
    PLACEHOLDER,
    SCRIPT_FILE,
    render_script,
    script_template,
)
from vfe_vision.domain.clip_paths import match_key

PATH = r"C:\Rushs\Été\clip.MP4"
HOSTILE = [
    '"; import os; os.system("calc") #',
    "'''; raise SystemExit('''",
    '"""; import os',
    "\\\\?\\C:\\n\\t\\x00 backslashes \\",
    "line one\nline two\r\n\u2028end",
    "</untrusted> {PAYLOAD} %s {0} ${x}",
    "é à ü 中文 😀",
]


def _payload(markers: list[dict[str, Any]], **clip: Any) -> dict[str, Any]:
    entry = {"video_id": "v1", "file_name": "clip.MP4", "path": PATH, "fps": 30.0,
             "duration_s": 120.0, "markers": markers, "metadata": {}}  # fmt: skip
    entry.update(clip)
    return {"format": "vfe-vision-resolve", "version": 1, "prefix": "vfe:",
            "case_insensitive": True, "clips": [entry]}  # fmt: skip


def _marker(
    kind: str, number: int, t_s: float, duration_s: float = 0.0, **extra: Any
) -> dict[str, Any]:
    return {"kind": kind, "t_s": t_s, "duration_s": duration_s, "color": "Blue",
            "name": f"{kind} {number}", "note": "", "custom_data": f"vfe:{kind}:{number}",
            **extra}  # fmt: skip


def _pool(*clips: FakeClip) -> FakeResolve:
    """Clips in nested bins, as in a real media pool."""
    inner = FakeFolder(clips=list(clips[1:]))
    return FakeResolve(
        FakeProject(FakeFolder(clips=list(clips[:1]), folders=[FakeFolder(folders=[inner])]))
    )


def test_the_script_is_the_shipped_file_and_pure_ascii() -> None:
    template = script_template()
    shipped = Path(__file__).parents[2] / "src/vfe_vision/adapters/resolve" / SCRIPT_FILE
    assert template == shipped.read_text(encoding="utf-8")
    assert template.count(PLACEHOLDER) == 1
    assert template.isascii()
    script = render_script(_payload([_marker("chapter", 1, 0.0, name="Chapitre é")]))
    assert script.isascii()  # run_script loses non-ASCII text (Resolve 21.1, Windows)
    assert script.replace(script.splitlines()[_line(script)], PLACEHOLDER) == template


def _line(script: str) -> int:
    return next(i for i, line in enumerate(script.splitlines()) if line.startswith("PAYLOAD = "))


@given(st.lists(st.text(), max_size=4))
def test_any_text_stays_data(texts: list[str]) -> None:
    payload = _payload([_marker("chapter", i + 1, float(i), name=t, note=t) for i, t in enumerate(texts)],
                       metadata={"Description": "".join(texts)})  # fmt: skip
    namespace = run_script(render_script(payload), FakeResolve(None))
    assert namespace["PAYLOAD"] == payload
    assert set(namespace) >= {"PAYLOAD", "result"}


def test_hostile_text_round_trips_as_data() -> None:
    markers = [_marker("highlight", i + 1, float(i), name=t, note=t) for i, t in enumerate(HOSTILE)]
    payload = _payload(markers, metadata={"Description": HOSTILE[0], "Comments": HOSTILE[2],
                                          "Keywords": HOSTILE})  # fmt: skip
    payload["clips"][0]["path"] = HOSTILE[3]
    script = render_script(payload)
    assert script.isascii()
    assert "import os" not in script.split("PAYLOAD = ", 1)[0]  # nothing before the data line
    namespace = run_script(script, FakeResolve(None))
    assert namespace["PAYLOAD"] == payload
    assert "os" not in namespace
    assert "SystemExit" not in str(namespace["result"])
    assert namespace["result"]["errors"] == ["aucun projet ouvert dans DaVinci Resolve"]
    clip = FakeClip(PATH, fps=30.0)
    payload["clips"][0]["path"] = PATH
    result = apply(payload, _pool(clip))
    assert result["applied"][0]["markers_added"] == len(HOSTILE)
    assert [m["name"] for m in clip.markers.values()] == HOSTILE
    assert clip.metadata["Description"] == HOSTILE[0]


def test_markers_in_frames_of_the_clips_own_rate() -> None:
    clip = FakeClip(r"\\?\C:\RUSHS\été\CLIP.mp4", fps=29.97, frames=3596)  # Resolve's rate
    payload = _payload([_marker("chapter", 1, 10.0, 20.0), _marker("shot", 3, 59.99)], fps=30.0)
    result = apply(payload, _pool(FakeClip(r"D:\other.mp4"), clip))
    assert result["not_found"] == []
    assert result["errors"] == []
    assert sorted(clip.markers) == [299, 1797]  # int(10 × 29.97), int(59.99 × 29.97)
    assert clip.markers[299]["duration"] == 599  # round(20 × 29.97)
    assert clip.markers[1797]["duration"] == 1
    assert clip.markers[299]["customData"] == "vfe:chapter:1"
    [applied] = result["applied"]
    assert (applied["fps"], applied["frames"], applied["warnings"]) == (29.97, 3596, [])


def test_running_again_replaces_only_its_own_markers() -> None:
    clip = FakeClip(PATH, fps=25.0)
    clip.AddMarker(250, "Red", "Mon repère", "", 1, "")  # the user's own marker
    clip.AddMarker(500, "Yellow", "Autre outil", "", 1, "other:1")
    first = apply(
        _payload([_marker("chapter", 1, 0.0, 30.0), _marker("highlight", 1, 20.0, 4.0)]),
        _pool(clip),
    )
    assert first["applied"][0]["markers_added"] == 2
    assert first["applied"][0]["markers_removed"] == 0
    before = clip.GetMarkers()
    again = apply(
        _payload([_marker("chapter", 1, 0.0, 30.0), _marker("highlight", 1, 20.0, 4.0)]),
        _pool(clip),
    )
    assert again["applied"][0]["markers_removed"] == 2
    assert clip.GetMarkers() == before  # idempotent
    fewer = apply(_payload([_marker("highlight", 1, 8.0, 2.0)]), _pool(clip))
    assert fewer["applied"][0]["markers_removed"] == 2
    assert sorted(clip.markers) == [200, 250, 500]  # the new one, and the others' markers
    assert clip.markers[250]["name"] == "Mon repère"
    assert clip.markers[500]["customData"] == "other:1"


def test_a_taken_frame_shifts_the_marker_and_says_so() -> None:
    clip = FakeClip(PATH, fps=25.0, frames=102)
    clip.AddMarker(50, "Red", "à moi", "", 1, "")
    result = apply(_payload([_marker("shot", 1, 2.0), _marker("shot", 2, 2.0), _marker("shot", 3, 5.0)],
                            duration_s=4.08), _pool(clip))  # fmt: skip
    [applied] = result["applied"]
    assert sorted(clip.markers) == [50, 51, 52]
    assert applied["markers_shifted"] == ["shot 1 : +1", "shot 2 : +2"]
    assert applied["markers_failed"] == ["shot 3 : après la fin du clip"]


def test_metadata_keeps_what_the_user_wrote() -> None:
    clip = FakeClip(PATH, metadata={"Keywords": "mariage, Drone", "Comments": "à revoir"})
    metadata = {"Keywords": ["plage", "drone", "chat"], "Description": "Un chat dort.",
                "Comments": "Lieu : Hyères"}  # fmt: skip
    first = apply(_payload([], metadata=metadata), _pool(clip))
    [applied] = first["applied"]
    assert clip.metadata["Keywords"] == "mariage, Drone, plage, chat"  # the user's stay first
    assert clip.metadata["Description"] == "Un chat dort."
    assert clip.metadata["Comments"] == "à revoir"  # the user's text is theirs
    assert applied["metadata_kept"] == ["Comments"]
    assert clip.third_party["vfe_video_id"] == "v1"
    assert json.loads(clip.third_party["VFE Vision"])["Keywords"] == ["plage", "chat"]

    clip.metadata["Keywords"] += ", famille"  # the user adds one; a new analysis changes ours
    second = apply(_payload([], metadata={**metadata, "Keywords": ["plage", "mer"],
                                          "Description": "Deux chats dorment."}), _pool(clip))  # fmt: skip
    assert clip.metadata["Keywords"] == "mariage, Drone, famille, plage, mer"
    assert clip.metadata["Description"] == "Deux chats dorment."  # still ours: updated
    assert second["applied"][0]["metadata_written"] == ["Description", "Keywords"]
    clip.metadata["Description"] = "Ma description"
    third = apply(_payload([], metadata=metadata), _pool(clip))
    assert clip.metadata["Description"] == "Ma description"
    assert set(third["applied"][0]["metadata_kept"]) == {"Comments", "Description"}


def test_a_refused_field_is_reported_and_the_others_written() -> None:
    clip = FakeClip(PATH, refuse=frozenset({"Comments"}))
    result = apply(_payload([], metadata={"Description": "d", "Comments": "c"}), _pool(clip))
    [applied] = result["applied"]
    assert applied["metadata_refused"] == ["Comments"]
    assert applied["metadata_written"] == ["Description"]
    assert clip.metadata == {"Description": "d"}


def test_not_found_other_length_and_bad_payloads() -> None:
    clip = FakeClip(PATH, fps=30.0, frames=1800)  # 60 s in Resolve, 120 s analysed
    result = apply(_payload([_marker("chapter", 1, 1.0)]), _pool(clip))
    assert "fréquence variable" in result["applied"][0]["warnings"][0]
    missing = apply(_payload([], path=r"C:\ailleurs\clip.mp4", file_name="clip.mp4"), _pool(clip))
    assert missing["not_found"] == [{"file": "clip.mp4", "path": r"C:\ailleurs\clip.mp4"}]
    assert apply({"format": "autre"}, _pool(clip))["errors"] == [
        "données absentes ou d'un autre format"
    ]
    assert apply({**_payload([]), "version": 2}, _pool(clip))["errors"] == [
        "version de données non prise en charge par ce script"
    ]
    menu = run_script(render_script(_payload([_marker("shot", 1, 1.0)])), _pool(clip), as_menu=True)
    assert menu["result"]["applied"][0]["markers_added"] == 1  # Workspace > Scripts: no project


def test_one_failing_clip_does_not_stop_the_others() -> None:
    broken = FakeClip(PATH, fps=0.0)
    sound = FakeClip(r"C:\Rushs\b.mp4")
    payload = _payload([_marker("shot", 1, 1.0)], fps=None)
    payload["clips"].append(
        {**payload["clips"][0], "path": r"C:\Rushs\b.mp4", "file_name": "b.mp4"}
    )
    result = apply(payload, _pool(broken, sound))
    assert result["errors"] == ["clip.MP4 : fréquence d'images inconnue"]
    assert result["applied"][0]["file"] == "b.mp4"


@pytest.mark.parametrize(
    ("path", "key"),
    [
        (r"C:\Rushs\Clip.MP4", "c:/rushs/clip.mp4"),
        (r"\\?\C:\Rushs\\Clip.MP4", "c:/rushs/clip.mp4"),
        ("c:/Rushs/./a/../Clip.mp4", "c:/rushs/clip.mp4"),
        (r"\\?\UNC\Serveur\Partage\x.mov", "//serveur/partage/x.mov"),
        (r"\\Serveur\Partage\x.mov", "//serveur/partage/x.mov"),
        ('"C:\\Rushs\\clip.mp4" ', "c:/rushs/clip.mp4"),
        ("/Volumes/Rushs/clip.mp4/", "/volumes/rushs/clip.mp4"),
    ],
)
def test_the_script_and_the_application_normalise_paths_alike(path: str, key: str) -> None:
    script = run_script(render_script({}), FakeResolve(None))
    assert match_key(path) == key
    assert script["match_key"](path) == key
    assert script["match_key"](path, False) == match_key(path, case_insensitive=False)


@given(st.text(alphabet=st.sampled_from("aZé/\\.?:UNC_ "), max_size=30))
def test_both_normalisations_agree_on_any_path(path: str) -> None:
    script = run_script(render_script({}), FakeResolve(None))
    assert script["match_key"](path) == match_key(path)
