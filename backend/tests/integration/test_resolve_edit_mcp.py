"""The assistant's Resolve tools through the MCP server: off until the user allows
them; read_timeline tied to the analyses; build_timeline (versioned name, the library's paths,
Transform values); apply_markers (the fixed script, run by the application); plan_reframe (the
head asked once to the vision model, then from the cache; contact sheets)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from mcp import Client

from tests.conftest import FakeLmStudio
from tests.fakes.library import Library, build_library
from tests.fakes.resolve_source import PROJECT, FakeResolve, clip, timeline
from vfe_vision.core.config import Settings
from vfe_vision.db.models import Keyframe, LlmCall, Video
from vfe_vision.db.preferences import update_preferences
from vfe_vision.db.session import Database
from vfe_vision.domain.edit_list import EditRequest, EditResult, PlacedItem
from vfe_vision.domain.resolve_timeline import ClipKind
from vfe_vision.mcp.server import build_mcp_server
from vfe_vision.services.container import AppContainer

pytestmark = pytest.mark.anyio
TOOLS = ("read_timeline", "build_timeline", "apply_markers", "plan_reframe")


class FakeEditor:
    """Stands for the editor child: records what it is asked, places every item."""

    def __init__(self) -> None:
        self.requests: list[EditRequest] = []
        self.scripts: list[str] = []

    def build_edit(self, request: EditRequest) -> EditResult:
        self.requests.append(request)
        placed, at = [], 108000
        for n, item in enumerate(request.items, 1):
            frames = round((item.out_s - item.in_s) * 25)
            placed.append(PlacedItem(n, f"item-{n}", at, at + frames, True,
                                     True if item.props else None))  # fmt: skip
            at += frames
        return EditResult(project=PROJECT, timeline_id="tl-new", timeline_name=request.name,
                          fps=25.0, width=request.width or 1920, height=request.height or 1080,
                          start_frame=108000, end_frame=at, items=placed,
                          audio_items=len(placed))  # fmt: skip

    def apply_markers(self, script: str) -> dict[str, Any]:
        self.scripts.append(script)
        return {"applied": [{"file": "vacances.mp4"}], "not_found": [], "errors": []}


@pytest.fixture
def editor() -> FakeEditor:
    return FakeEditor()


@pytest.fixture
def c(
    settings: Settings, db: Database, editor: FakeEditor, fake_lmstudio: FakeLmStudio
) -> Iterator[AppContainer]:
    container = AppContainer.create(settings)
    container.resolve = FakeResolve()
    container.resolve_editor = editor
    container.lmstudio = fake_lmstudio.client()
    yield container
    container.db.dispose()


def _library(c: AppContainer, tmp_path: Path) -> tuple[Library, Video]:
    library = build_library(c.db, tmp_path / "Rushs")
    with c.db.read() as session:
        holiday = session.get_one(Video, library.holiday)
        frames = session.query(Keyframe).filter_by(video_id=holiday.id).all()
        paths = [c.artifacts.resolve(f.image_path) for f in frames]
    for path in paths:  # the keyframe images the vision model and the sheets read
        path.parent.mkdir(parents=True, exist_ok=True)
        picture = np.full((1080, 1920, 3), 90, dtype=np.uint8)
        cv2.rectangle(picture, (384, 324), (1344, 972), (30, 120, 200), -1)
        cv2.imwrite(str(path), picture)
    return library, holiday


def _text(result: Any) -> str:
    return "\n".join(part.text for part in result.content if getattr(part, "text", None))


async def test_tools_are_listed_and_off_until_allowed(c: AppContainer, tmp_path: Path) -> None:
    library, _ = _library(c, tmp_path)
    async with Client(build_mcp_server(lambda: c)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        refused = await client.call_tool("build_timeline", {"name": "X", "items": [
            {"video_id": library.holiday, "in_s": 0, "out_s": 2}]})  # fmt: skip
    for name in TOOLS:
        assert tools[name].description, name
    assert refused.is_error
    assert "Connexions" in _text(refused)


async def test_read_then_build_a_new_timeline(
    c: AppContainer, editor: FakeEditor, tmp_path: Path
) -> None:
    library, holiday = _library(c, tmp_path)
    resolve: FakeResolve = c.resolve  # type: ignore[assignment]
    resolve.put(timeline("tl-1", "Chats", current=True), [
        clip(holiday.path, 0, 4, source=(40.0, 44.0)),
        clip(None, 4, 5, kind=ClipKind.GRAPHICS),
    ])  # fmt: skip
    resolve.put(timeline("tl-2", "Chats - vfe v2"), [])
    update_preferences(c.db, {"mcp_resolve_tools": True})
    async with Client(build_mcp_server(lambda: c)) as client:
        read = await client.call_tool("read_timeline", {})
        built = await client.call_tool("build_timeline", {"name": "Chats", "items": [
            {"video_id": library.holiday, "in_s": 40, "out_s": 44,
             "props": {"ZoomX": 3.1605, "Pan": 12.5, "Tilt": -480}},
            {"media_pool_item_id": "pool-x", "in_s": 0, "out_s": 2, "video_only": True},
        ], "timeline_width": 1920, "timeline_height": 1080})  # fmt: skip
        backwards = await client.call_tool("build_timeline", {"name": "X", "items": [
            {"video_id": library.holiday, "in_s": 4, "out_s": 2}]})  # fmt: skip
    data = read.structured_content or {}
    first, title = data["clips"]
    assert (first["video_id"], first["status"], first["track"]) == (holiday.id, "ready", "V1")
    assert (first["source_start_s"], first["source_end_s"]) == (40.0, 44.0)
    assert (first["start_s"], first["end_s"]) == (0.0, 4.0)
    assert title["kind"] == "graphics"
    assert "video_id" not in title
    assert "BEGIN UNTRUSTED VIDEO CONTENT" in _text(read)

    [request] = editor.requests
    assert request.name == "Chats - vfe v3"  # after the versions already in the project
    assert (request.width, request.height) == (1920, 1080)
    one, two = request.items
    assert one.path == holiday.path
    assert one.video_id == holiday.id
    assert dict(one.props or {}) == {"ZoomX": 3.1605, "Pan": 12.5, "Tilt": -480.0, "ZoomY": 3.1605}
    assert (two.media_pool_item_id, two.path, two.video_only) == ("pool-x", None, True)
    result = built.structured_content or {}
    assert result["timeline"]["name"] == "Chats - vfe v3"
    placed_one, placed_two = result["placed"]
    assert placed_one["props_ok"] is True
    assert "props_ok" not in placed_two  # no Transform asked
    assert (placed_two["start_s"], placed_two["end_s"]) == (4.0, 6.0)
    assert result["checks_failed"] == 0
    assert "Ctrl+S" in _text(built)
    assert backwards.is_error


async def test_markers_run_by_the_application(
    c: AppContainer, editor: FakeEditor, tmp_path: Path
) -> None:
    library, _ = _library(c, tmp_path)
    update_preferences(c.db, {"mcp_resolve_tools": True})
    async with Client(build_mcp_server(lambda: c)) as client:
        done = await client.call_tool("apply_markers", {"video_ids": [library.holiday]})
    [script] = editor.scripts
    assert script.isascii()
    assert "SCRIPT_VERSION = 1" in script
    assert (done.structured_content or {})["applied"] == 1


async def test_plan_reframe_asks_the_head_once(
    c: AppContainer, fake_lmstudio: FakeLmStudio, tmp_path: Path
) -> None:
    library, _ = _library(c, tmp_path)
    update_preferences(c.db, {"mcp_resolve_tools": True})
    fake_lmstudio.answers = [json.dumps({"found": True, "bbox_2d": [300, 320, 450, 520]})]
    item = {"video_id": library.holiday, "in_s": 0, "out_s": 10}
    async with Client(build_mcp_server(lambda: c)) as client:
        first = await client.call_tool("plan_reframe", {
            "items": [item], "timeline_width": 1080, "timeline_height": 1920, "subject": "chat",
            "sheets": "all"})  # fmt: skip
        again = await client.call_tool("plan_reframe", {
            "items": [item], "timeline_width": 1080, "timeline_height": 1920, "subject": "chat",
            "sheets": "none"})  # fmt: skip
    plan = first.structured_content or {}
    # The cat is wider than a 9:16 crop: its head is what the crop aims at.
    assert plan["heads"] | {"model": None} == {
        "model": None, "wanted": 1, "from_faces": 0, "cached": 0, "asked": 1, "found": 1,
        "failed": 0, "pending": 0,
    }  # fmt: skip
    [piece] = plan["pieces"]
    assert piece["head_kept"] == 1.0
    assert piece["props"]["ZoomX"] == pytest.approx(3.1605, abs=1e-3)
    [edit_item] = plan["edit_items"]
    assert edit_item["props"] == piece["props"]
    assert edit_item["video_id"] == library.holiday
    assert [part.type for part in first.content] == ["text", "image"]  # one contact sheet
    heads_asked = [r for r in fake_lmstudio.chat_requests if "head" in json.dumps(r)]
    assert len(heads_asked) == 1  # the second plan read the cache
    assert (again.structured_content or {})["heads"]["cached"] == 1
    with c.db.read() as session:
        assert session.query(LlmCall).filter_by(purpose="head").count() == 1


async def test_plan_reframe_without_lm_studio(
    c: AppContainer, fake_lmstudio: FakeLmStudio, tmp_path: Path
) -> None:
    library, _ = _library(c, tmp_path)
    update_preferences(c.db, {"mcp_resolve_tools": True})
    fake_lmstudio.down = True
    async with Client(build_mcp_server(lambda: c)) as client:
        done = await client.call_tool("plan_reframe", {
            "items": [{"video_id": library.holiday, "in_s": 0, "out_s": 10}],
            "timeline_width": 1080, "timeline_height": 1920, "subject": "chat"})  # fmt: skip
    plan = done.structured_content or {}
    assert plan["heads"]["pending"] == 1
    assert "LM Studio" in plan["heads"]["note"]
    assert "head_pending" in plan["pieces"][0]["flags"]
