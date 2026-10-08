"""MCP tools through the official in-memory client."""

from __future__ import annotations

import base64
import io
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import sqlalchemy as sa
from mcp import Client
from PIL import Image as PILImage

from tests.conftest import FRAME_ANSWER
from vfe_vision.adapters.imaging import write_jpeg
from vfe_vision.core.config import Settings
from vfe_vision.db.models import (
    Detection,
    FrameAnalysis,
    Keyframe,
    LibraryRoot,
    Shot,
    ShotStory,
    Video,
)
from vfe_vision.domain.enums import VideoStatus
from vfe_vision.mcp.server import INSTRUCTIONS, build_mcp_server
from vfe_vision.services.container import AppContainer

pytestmark = pytest.mark.anyio


@pytest.fixture
async def container(
    settings: Settings, db: object, library_folder: Path
) -> AsyncIterator[AppContainer]:
    c = AppContainer.create(settings)
    video_file = next(library_folder.iterdir())
    with c.db.write() as session:
        root = LibraryRoot(
            path=str(library_folder), path_key=str(library_folder).lower(), label="t"
        )
        session.add(root)
        session.flush()
        video = Video(
            root_id=root.id, path=str(video_file), path_key=str(video_file).lower(),
            rel_path=video_file.name, filename=video_file.name, size_bytes=1, mtime=0.0,
            fingerprint="f", status=VideoStatus.READY, duration_s=6.0, width=320, height=240,
            encoder="Blackmagic Design DaVinci Resolve Studio",
            # What the metadata stage stores for an editor export.
            captured_at=datetime(2025, 7, 1, 23, 27, tzinfo=UTC),
            captured_at_source="export_date", captured_at_confidence="low",
        )  # fmt: skip
        session.add(video)
        session.flush()
        for idx, t in enumerate((0.0, 2.0, 4.0)):
            rel = f"{video.id[-2:]}/{video.id}/keyframes/kf_{idx:04d}.jpg"
            write_jpeg(c.artifacts.resolve(rel), np.full((240, 320, 3), idx * 80, np.uint8))
            frame = Keyframe(
                video_id=video.id, idx=idx, t_s=t, image_path=rel, thumb_path=rel,
                width=320, height=240, selection_reason="scene",
            )  # fmt: skip
            session.add(frame)
            session.flush()
            data = {**FRAME_ANSWER, "caption": f"Plan {idx}", "visible_text": "SOLDES -50%"}
            session.add(
                FrameAnalysis(
                    keyframe_id=frame.id,
                    model="qwen/qwen3-vl-8b",
                    prompt_version="v1",
                    schema_version=1,
                    language="fr",
                    data=data,
                )
            )
        c.worker_pid = None
    yield c
    await c.aclose()


async def test_tools_are_listed_with_schemas(container: AppContainer) -> None:
    async with Client(build_mcp_server(lambda: container)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    expected = {"watch_video", "get_frames", "list_watched", "get_video", "get_job"}
    assert expected | {"get_video_context"} <= set(tools)
    assert tools["list_watched"].output_schema is not None


async def test_the_instructions_name_every_tool(container: AppContainer) -> None:
    """A client without the vfe-montage skill (Cursor, Codex, VS Code) learns the tools from
    the server's instructions: none is left out, the Resolve tools of the assistant included."""
    async with Client(build_mcp_server(lambda: container)) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
    assert {"read_timeline", "build_timeline", "plan_reframe", "apply_markers"} <= tools
    assert [name for name in sorted(tools) if name not in INSTRUCTIONS] == []


async def test_manifest_and_frames(container: AppContainer) -> None:
    async with Client(build_mcp_server(lambda: container)) as client:
        listed = await client.call_tool("list_watched", {})
        video_id = listed.structured_content["result"][0]["id"]

        manifest = await client.call_tool("get_video", {"video_id": video_id})
        text = manifest.content[0].text  # type: ignore[union-attr]
        assert "[00:02.000] image 2 — Plan 1" in text
        assert "date du fichier est celle de l'export" in text
        assert "BEGIN UNTRUSTED VIDEO CONTENT" in text  # on-screen text is fenced

        frames = await client.call_tool("get_frames", {"video_id": video_id, "timestamps": [3.9]})
        kinds = [c.type for c in frames.content]
        assert kinds == ["text", "image"]
        assert "t=4.000s" in frames.content[0].text  # type: ignore[union-attr]


async def test_errors_are_tool_errors(container: AppContainer) -> None:
    async with Client(build_mcp_server(lambda: container)) as client:
        result = await client.call_tool("get_job", {"job_id": "missing"})
    assert result.is_error
    assert "introuvable" in result.content[0].text  # type: ignore[union-attr]


async def test_watch_video_outside_library_is_refused(
    container: AppContainer, tmp_path: Path
) -> None:
    outside = tmp_path / "ailleurs.mp4"
    outside.write_bytes(b"\x00" * 16)
    async with Client(build_mcp_server(lambda: container)) as client:
        result = await client.call_tool("watch_video", {"path": str(outside), "wait_s": 0})
    assert result.is_error
    assert "aucun dossier" in result.content[0].text  # type: ignore[union-attr]


async def test_watch_video_network_path_is_refused_before_any_access(
    container: AppContainer,
) -> None:
    """A path outside the library is refused on its text: a network share is never opened."""
    async with Client(build_mcp_server(lambda: container)) as client:
        result = await client.call_tool(
            "watch_video", {"path": r"\\attacker.invalid\share\clip.mp4", "wait_s": 0}
        )
    assert result.is_error
    assert "aucun dossier" in result.content[0].text  # type: ignore[union-attr]


async def test_object_locations(container: AppContainer) -> None:
    """Positions reach the caller as structured data, keyword options included."""
    with container.db.write() as session:
        frames = session.execute(sa.select(Keyframe).order_by(Keyframe.idx)).scalars().all()
        for frame in frames[:2]:
            session.add(
                Detection(
                    keyframe_id=frame.id, video_id=frame.video_id, t_s=frame.t_s, source="vlm",
                    idx=0, label="chat", category="mammal", box=[0.25, 0.5, 0.75, 1.0],
                    score=None, main=True, model="qwen/qwen3-vl-8b",
                )
            )  # fmt: skip
        video_id = frames[0].video_id
    async with Client(build_mcp_server(lambda: container)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert tools["get_object_locations"].output_schema is not None
        result = await client.call_tool(
            "get_object_locations", {"video_id": video_id, "start_s": 1.0, "main_only": True}
        )
        manifest = await client.call_tool("get_video", {"video_id": video_id})
    data = result.structured_content
    assert data is not None
    # The keyframe at 0 s stands until 2 s: it covers the start of the interval too.
    assert [(f["t_s"], f["until_s"]) for f in data["frames"]] == [(0.0, 2.0), (2.0, 4.0)]
    assert data["sampled"] is False
    assert "face_points" not in data["frames"][0]["subjects"][0]  # only on request
    assert result.content[0].text.startswith('{"video_id":')  # type: ignore[union-attr]
    subject = data["frames"][1]["subjects"][0]
    assert subject["box_px"] == [80, 120, 240, 240]  # 320×240
    assert subject["main"] is True
    text = manifest.content[0].text  # type: ignore[union-attr]
    assert "- sujets vivants : chat — sur 2 images distinctes" in text


async def test_shots_tell_which_frames_get_frames_can_show(container: AppContainer) -> None:
    """The manifest says the stories are generated and points to get_shots; get_shots numbers
    a noted keyframe like get_frames, and gives no number to a frame extracted for the story."""
    summary = " ".join(["Des maisons aux toits rouges"] * 8)  # longer than a manifest line
    with container.db.write() as session:
        frames = session.execute(sa.select(Keyframe).order_by(Keyframe.idx)).scalars().all()
        video_id = frames[0].video_id
        shot = Shot(
            video_id=video_id, idx=0, start_s=0.0, end_s=6.0, boundary="start",
            motion="pan_left", motion_score=1.5, stability=0.9,
        )  # fmt: skip
        session.add(shot)
        session.flush()
        session.add(
            ShotStory(
                video_id=video_id, shot_id=shot.id, part=1, parts=1, start_s=0.0, end_s=6.0,
                frame_times=[2.0, 3.1], keyframe_ids=[frames[1].id, None],
                frame_paths=[frames[1].thumb_path, "extracted.jpg"], model="qwen/qwen3-vl-8b",
                prompt_version="shot_story.v1", schema_version=1, language="fr", answer={},
                story={"summary": summary, "main_action": "", "possible_cut": False,
                       "notes": [{"t_s": 2.0, "what": "un toit"}, {"t_s": 3.1, "what": "la rue"}]},
            )
        )  # fmt: skip
    async with Client(build_mcp_server(lambda: container)) as client:
        manifest = await client.call_tool("get_video", {"video_id": video_id})
        shots = await client.call_tool("get_shots", {"video_id": video_id})
    text = manifest.content[0].text  # type: ignore[union-attr]
    assert (
        "## Plans (1) — mouvement mesuré ; récits : modèle de vision, peuvent se tromper ; "
        "détail : get_shots"
    ) in text
    line = next(row for row in text.splitlines() if "plan 1 —" in row)
    told = line.split(" · ", 1)[1]
    assert len(told) <= 160
    assert told.endswith("…")
    assert summary[len(told) - 1] == " "  # cut between two words, not inside one
    data = shots.structured_content
    assert data is not None
    [part] = data["shots"][0]["parts"]
    assert [(f["seen_at_s"], f["keyframe"]) for f in part["frames"]] == [(2.0, 2), (3.1, None)]


async def test_frames_of_chosen_shots_at_a_smaller_size(container: AppContainer) -> None:
    """get_frames also takes shot numbers (the sharpest keyframe of each) and an image size."""
    with container.db.write() as session:
        frames = session.execute(sa.select(Keyframe).order_by(Keyframe.idx)).scalars().all()
        video_id = frames[0].video_id
        for idx, (start, end) in enumerate(((0.0, 3.0), (3.0, 6.0))):
            shot = Shot(
                video_id=video_id, idx=idx, start_s=start, end_s=end, boundary="cut",
                motion="static", motion_score=0.1, stability=1.0, metrics={},
            )  # fmt: skip
            session.add(shot)
            session.flush()
            for frame in frames:
                if start <= frame.t_s < end:
                    frame.shot_id = shot.id
        frames[0].sharpness, frames[1].sharpness = 10.0, 50.0  # shot 1: the keyframe at 2 s
    async with Client(build_mcp_server(lambda: container)) as client:
        result = await client.call_tool(
            "get_frames", {"video_id": video_id, "shots": [2, 1, 9], "size": 256}
        )
        none = await client.call_tool("get_frames", {"video_id": video_id, "shots": [9]})
    labels = [c.text for c in result.content if c.type == "text"]
    assert [label.split(" @ ")[0] for label in labels] == ["image 3", "image 2"]
    images = [c for c in result.content if c.type == "image"]
    for image in images:
        width, height = PILImage.open(io.BytesIO(base64.b64decode(image.data))).size
        assert max(width, height) == 256
    assert "Aucune image clé pour ces plans" in none.content[0].text  # type: ignore[union-attr]
