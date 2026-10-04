"""Merging analysis requests and listing what an ordinary analysis would do."""

from __future__ import annotations

from vfe_vision.db.models import StageRun
from vfe_vision.domain.enums import StageStatus
from vfe_vision.jobs.queue import merge_analysis, union_scope
from vfe_vision.pipeline.stages import default_registry
from vfe_vision.services.videos import analysis_gaps


def test_union_scope() -> None:
    assert union_scope(False, None) is False
    assert union_scope(["b"], ["a", "b"]) == ["a", "b"]
    assert union_scope(["a"], True) is True


def test_merged_requests_do_what_both_asked() -> None:
    queued = {"mode": "complete", "force": False, "refresh": False}
    merged = merge_analysis(
        queued, {"mode": "full", "force": ["transcript"], "stages": ["transcript"]}
    )
    # Every stage stays requested, only the transcript is redone.
    assert merged == {"mode": "full", "force": ["transcript"], "refresh": False}
    both = merge_analysis(
        {"stages": ["ocr"], "force": ["ocr"], "focus": "oiseaux"},
        {"stages": ["transcript"], "refresh": ["transcript"]},
    )
    assert both == {
        "stages": ["ocr", "transcript"], "force": ["ocr"], "refresh": ["transcript"],
        "focus": "oiseaux",
    }  # fmt: skip


def _run(
    stage: str, status: StageStatus, *, version: int, key: str = "k", **summary: object
) -> StageRun:
    return StageRun(
        stage=stage, stage_version=version, status=status, cache_key=key, summary=dict(summary)
    )


def test_gaps_list_missing_and_outdated_stages() -> None:
    versions = {stage.name: stage.version for stage in default_registry().plan()}
    runs = [
        _run(name, StageStatus.SUCCEEDED, version=version) for name, version in versions.items()
    ]
    assert analysis_gaps(runs).missing == []
    runs[0] = _run("probe", StageStatus.SUCCEEDED, version=versions["probe"] - 1)
    runs[1] = _run("metadata", StageStatus.SKIPPED, version=versions["metadata"], retryable=True)
    runs[2] = _run("place", StageStatus.SUCCEEDED, version=versions["place"], key="")  # invalidated
    runs[3] = _run("weather", StageStatus.FAILED, version=versions["weather"])
    gaps = analysis_gaps(runs[:-1])  # the last stage never ran
    assert gaps.outdated == ["probe"]
    assert gaps.missing == ["metadata", "place", "weather", list(versions)[-1]]
