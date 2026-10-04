"""Library roots (declared folders) and the Resolve timelines brought in."""

from __future__ import annotations

from fastapi import APIRouter, status

from vfe_vision.api.deps import Container
from vfe_vision.api.schemas import (
    BatchAnalyzeOut,
    JobOut,
    RootAnalyzeOut,
    RootAnalyzeRequest,
    RootCreate,
    RootCreated,
    RootFoldersOut,
    RootOut,
    RootUpdate,
    TimelineBinOut,
    TimelineBinPatch,
    TimelineImportOut,
    TimelineItemOut,
)
from vfe_vision.services import analysis, library, timeline_bins

router = APIRouter(prefix="/library", tags=["library"])


@router.get("/roots")
def list_roots(c: Container) -> list[RootOut]:
    return [RootOut.from_stats(stats) for stats in library.list_roots(c)]


@router.get("/folders")
def list_folders(c: Container) -> list[RootFoldersOut]:
    """The folders of each root, like DaVinci Resolve's bins."""
    return [RootFoldersOut.of(item) for item in library.folder_tree(c)]


@router.post("/roots", status_code=status.HTTP_201_CREATED)
def add_root(c: Container, body: RootCreate) -> RootCreated:
    root, job = library.add_root(
        c,
        body.path,
        label=body.label,
        recursive=body.recursive,
        analysis_focus=body.analysis_focus,
        auto_analyze=body.auto_analyze,
    )
    stats = next(s for s in library.list_roots(c) if s.root.id == root.id)
    return RootCreated(root=RootOut.from_stats(stats), scan_job=JobOut.of(job))


@router.patch("/roots/{root_id}")
def update_root(c: Container, root_id: str, body: RootUpdate) -> RootOut:
    patch = body.model_dump(exclude_unset=True)
    inputs_changed = analysis.root_inputs_differ(library.get_root(c, root_id), patch)
    library.update_root(c, root_id, patch)
    if inputs_changed:  # e.g. a clock correction: capture times, weather and sun follow
        analysis.root_inputs_changed(c, root_id)
    stats = next(s for s in library.list_roots(c) if s.root.id == root_id)
    return RootOut.from_stats(stats)


@router.post("/roots/{root_id}/analyze", status_code=status.HTTP_202_ACCEPTED)
def analyze_root(c: Container, root_id: str, body: RootAnalyzeRequest) -> RootAnalyzeOut:
    """Analyse the folder's videos. By default only what is missing is done."""
    return RootAnalyzeOut(queued=analysis.analyze_root(c, root_id, mode=body.mode))


@router.post("/translate", status_code=status.HTTP_202_ACCEPTED)
def translate_library(c: Container) -> BatchAnalyzeOut:
    """Translate the analyses already made into French and English, without redoing them:
    one « Translation » job per video that has texts not translated yet. New analyses are
    translated on their own."""
    result = analysis.translate_library(c)
    return BatchAnalyzeOut(
        queued=result.queued,
        up_to_date=result.up_to_date,
        offline=result.offline,
        unknown=list(result.unknown),
    )


@router.delete("/roots/{root_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_root(c: Container, root_id: str) -> None:
    library.remove_root(c, root_id)


@router.post("/roots/{root_id}/scan", status_code=status.HTTP_202_ACCEPTED)
def scan_root(c: Container, root_id: str) -> JobOut:
    return JobOut.of(library.request_scan(c, root_id))


@router.get("/timelines")
def list_timelines(c: Container) -> list[TimelineBinOut]:
    """The DaVinci Resolve timelines added to the library, by name, with the status of their
    files and their last update."""
    return [TimelineBinOut.of(stats) for stats in timeline_bins.list_bins(c)]


@router.get("/timelines/{bin_id}/items")
def list_timeline_items(c: Container, bin_id: str) -> list[TimelineItemOut]:
    """The timeline's files in the order it uses them, with their status."""
    return [TimelineItemOut.of(item) for item in timeline_bins.bin_items(c, bin_id)]


@router.patch("/timelines/{bin_id}")
def update_timeline(c: Container, bin_id: str, body: TimelineBinPatch) -> TimelineBinOut:
    return TimelineBinOut.of(
        timeline_bins.update_bin(c, bin_id, body.model_dump(exclude_unset=True))
    )


@router.delete("/timelines/{bin_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_timeline(c: Container, bin_id: str) -> None:
    """Take the timeline out of the library: no video or analysis is forgotten, nothing
    changes in Resolve."""
    timeline_bins.remove_bin(c, bin_id)


@router.post("/timelines/{bin_id}/sync", status_code=status.HTTP_202_ACCEPTED)
def sync_timeline(c: Container, bin_id: str) -> TimelineImportOut:
    """« Update from Resolve »: read the timeline again (its project must be open in Resolve;
    409 otherwise) and reflect it as it is now."""
    return TimelineImportOut.of(timeline_bins.sync_bin(c, bin_id))
