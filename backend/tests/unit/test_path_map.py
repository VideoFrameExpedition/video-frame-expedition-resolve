"""Folders seen from this computer and from the one DaVinci Resolve runs on."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from vfe_vision.domain.path_map import FolderPair, from_resolve, to_resolve
from vfe_vision.domain.preferences import AnalysisPreferences, folder_pairs

PAIRS = [
    FolderPair(here="D:\\cats 2026", there="/Volumes/cats 2026"),
    FolderPair(here="D:\\cats 2026\\hdr", there="/Volumes/HDR"),  # the longest pair wins
    FolderPair(here="\\\\nas\\rushs", there="/Volumes/rushs/"),
]


def test_paths_go_to_resolve_and_back() -> None:
    assert to_resolve("D:\\cats 2026\\apv\\a.mp4", PAIRS) == "/Volumes/cats 2026/apv/a.mp4"
    assert to_resolve("d:/CATS 2026/hdr/Été.mp4", PAIRS) == "/Volumes/HDR/Été.mp4"
    assert from_resolve("/Volumes/cats 2026/apv/a.mp4", PAIRS) == "D:\\cats 2026\\apv\\a.mp4"
    assert from_resolve("/volumes/hdr/Été.mp4", PAIRS) == "D:\\cats 2026\\hdr\\Été.mp4"
    assert from_resolve("/Volumes/rushs/x.mov", PAIRS) == "\\\\nas\\rushs\\x.mov"
    assert to_resolve("D:\\cats 2026", PAIRS) == "/Volumes/cats 2026"


def test_paths_no_pair_holds_stay_unmapped() -> None:
    assert to_resolve("C:\\Users\\me\\Videos\\a.mp4", PAIRS) is None
    assert from_resolve("/Users/me/Movies/a.mp4", PAIRS) is None
    assert to_resolve("D:\\cats 2026 bis\\a.mp4", PAIRS) is None  # a folder, not a prefix
    assert from_resolve("/Volumes/cats 2026/a.mp4", []) is None


def test_resolve_settings_are_checked() -> None:
    prefs = AnalysisPreferences.model_validate(
        {
            "resolve_host": "100.64.1.2",
            "resolve_folders": [{"here": "D:\\a", "there": "/Volumes/a"}],
        }
    )
    assert folder_pairs(prefs) == [FolderPair(here="D:\\a", there="/Volumes/a")]
    assert AnalysisPreferences().resolve_host is None  # Resolve on this computer by default
    for host in ("http://mac/", "mac studio", "a;b"):
        with pytest.raises(ValidationError):
            AnalysisPreferences.model_validate({"resolve_host": host})
