"""Shared fixtures: isolated settings/database, generated sample videos, fake LM Studio."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest

from vfe_vision.adapters.lmstudio.client import LmStudioClient
from vfe_vision.core.config import Settings
from vfe_vision.db.migrate import upgrade_database
from vfe_vision.db.session import Database

# The terminal's messages in French whatever the system's language (core.language): the tests
# read them, and the help texts are chosen when the command-line module is imported.
os.environ["VFE_LANG"] = "fr"

FIXTURES = Path(__file__).parent / "fixtures"
GENERATED = FIXTURES / "generated"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        worker_enabled=False,
        lmstudio_url="http://lmstudio.test",
        # Online services never reached from tests (fakes are injected where needed).
        nominatim_url="http://nominatim.test",
        open_meteo_historical_url="http://open-meteo.test/v1/forecast",
        open_meteo_archive_url="http://open-meteo.test/v1/archive",
        log_level="WARNING",
        nvidia_smi_path="nvidia-smi-disabled-in-tests",  # the GPU is never lent in tests
        cpu_workers=2,
        max_concurrent_videos=1,
    )


@pytest.fixture
def db(settings: Settings) -> Iterator[Database]:
    settings.ensure_dirs()
    upgrade_database(settings.db_path, settings.backups_dir)
    database = Database(settings.db_path)
    yield database
    database.dispose()


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@pytest.fixture(scope="session")
def sample_video() -> Path:
    """6 s, 320×240, three distinct 2 s shots, a sine tone, a date and a GPS position (Paris).

    Cached on disk: change the file name when the recipe changes.
    """
    GENERATED.mkdir(parents=True, exist_ok=True)
    target = GENERATED / "sample_cuts_gps.mp4"
    if target.exists():
        return target
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=2",
        "-f", "lavfi", "-i", "smptebars=size=320x240:rate=25:duration=2",
        "-f", "lavfi", "-i", "color=c=0x2266cc:size=320x240:rate=25:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
        "-map", "[v]", "-map", "3:a", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", "-metadata", "creation_time=2025-07-14T16:30:00Z",
        "-metadata", "location=+48.8584+002.2945/",
        str(target),
    )  # fmt: skip
    return target


@pytest.fixture
def library_folder(tmp_path: Path, sample_video: Path) -> Path:
    """A library folder with the sample video, once under an accented name."""
    folder = tmp_path / "Rushs été"
    folder.mkdir()
    shutil.copy2(sample_video, folder / "plan séquence.mp4")
    return folder


# ---------------------------------------------------------------- fake LM Studio
FRAME_ANSWER: dict[str, Any] = {
    "caption": "Mire de test colorée.",
    "description": "Une mire de test synthétique occupe toute l'image.",
    "shot_type": "wide",
    "camera_angle": "eye_level",
    "setting": "unknown",
    "place_type": "studio",
    "time_of_day": "not_visible",
    "weather": "sky_not_visible",
    "lighting": "artificial",
    "subjects": [{"label": "mire", "description": "motif de test", "is_main": True}],
    "people_count": 0,
    "actions": [],
    "mood": "neutre",
    "dominant_colors": ["rouge", "vert", "bleu"],
    "visible_text": "",
    "quality_issues": [],
    "editing_value": ["b_roll"],
    "tags": ["test", "mire"],
}

MODELS_PAYLOAD: dict[str, Any] = {
    "models": [
        {
            "type": "llm",
            "publisher": "qwen",
            "key": "qwen/qwen3-vl-8b",
            "display_name": "Qwen3 VL 8B",
            "architecture": "qwen3vl",
            "quantization": {"name": "Q8_0", "bits_per_weight": 8},
            "size_bytes": 9_870_000_000,
            "params_string": "8B",
            "loaded_instances": [
                {"id": "qwen/qwen3-vl-8b", "config": {"context_length": 10496, "parallel": 4}}
            ],
            "max_context_length": 262144,
            "capabilities": {"vision": True, "trained_for_tool_use": True},
        },
        {
            "type": "embedding",
            "key": "text-embedding-nomic-embed-text-v1.5",
            "display_name": "Nomic Embed",
            "loaded_instances": [],
        },
    ]
}

# LM Studio with a model downloaded in three variants, loaded from one that is not the selected
# one (Q6_K, Q4_K_M selected): the main list shows no instance (checked against the real server).
VARIANTS_PAYLOAD: dict[str, Any] = {
    "models": [
        {
            "type": "llm",
            "publisher": "qwen",
            "key": "qwen/qwen3-vl-4b",
            "display_name": "Qwen3 VL 4B",
            "architecture": "qwen3vl",
            "quantization": {"name": "Q4_K_M", "bits_per_weight": 4},
            "size_bytes": 3_333_641_502,
            "params_string": "4B",
            "loaded_instances": [],
            "max_context_length": 262144,
            "format": "gguf",
            "capabilities": {"vision": True, "trained_for_tool_use": True},
            "variants": [
                "qwen/qwen3-vl-4b@q4_k_m",
                "qwen/qwen3-vl-4b@q6_k",
                "qwen/qwen3-vl-4b@q8_0",
            ],
            "selected_variant": "qwen/qwen3-vl-4b@q4_k_m",
        },
        {
            "type": "llm",
            "publisher": "qwen",
            "key": "qwen/qwen3-vl-8b",
            "display_name": "Qwen3 VL 8B",
            "loaded_instances": [],
            "capabilities": {"vision": True},
            "variants": ["qwen/qwen3-vl-8b@q8_0"],
            "selected_variant": "qwen/qwen3-vl-8b@q8_0",
        },
    ]
}

# The older list of the same server (GET /api/v0/models): the model is loaded.
OLDER_PAYLOAD: dict[str, Any] = {
    "data": [
        {
            "id": "qwen/qwen3-vl-4b",
            "object": "model",
            "type": "vlm",
            "quantization": "Q4_K_M",
            "state": "loaded",
            "max_context_length": 262144,
            "loaded_context_length": 20224,
        },
        {"id": "qwen/qwen3-vl-8b", "object": "model", "type": "vlm", "state": "not-loaded"},
    ]
}


SHOT_ANSWER: dict[str, Any] = {
    "summary": "La mire de test défile puis laisse place à des barres de couleur.",
    "main_action": "",
    "beats": [{"image": 2, "what": "les barres apparaissent"}],
    "camera": "still",
    "continuity": "same_subject",
    "best_image": 1,
}


def answer_for_schema(schema: dict[str, Any], name: str = "") -> Any:
    """A valid answer built from a strict JSON schema (the synthesis asks per-video shapes)."""
    kind = schema.get("type")
    if kind == "object":
        return {k: answer_for_schema(v, k) for k, v in schema.get("properties", {}).items()}
    if kind == "array":
        count = max(int(schema.get("minItems", 0)), 2 if name == "tags" else 0)
        return [answer_for_schema(schema.get("items", {}), name) for _ in range(count)]
    if "enum" in schema:
        return schema["enum"][0]
    if kind == "integer":
        return int(schema.get("minimum", 1))
    if kind == "number":
        return 0.5
    if kind == "boolean":
        return False
    words = {"title": "Un titre de test", "tags": "mire", "logline": "Une vidéo de test."}
    return words.get(name, f"Texte de test pour {name}.")


def translated_text(text: str, target: str) -> str:
    """What the fake model answers for a translation: a text marked ``EN:`` is written
    in English (given back as it is into English, ``FR:`` into French); any other text gets its
    target in brackets."""
    if text.startswith("EN:"):
        return text if target == "en" else "FR:" + text[3:]
    return f"{text} [{target}]"


def _translation_answer(body: dict[str, Any]) -> dict[str, Any]:
    user = body["messages"][1]["content"]
    text = user if isinstance(user, str) else "".join(p.get("text", "") for p in user)
    head, body_text = text.split("\n", 1)
    target = "en" if "into English" in head else "fr"
    texts: dict[str, str] = json.loads(body_text)
    return {key: translated_text(value, target) for key, value in texts.items()}


def _default_answer(body: dict[str, Any]) -> dict[str, Any]:
    """A frame description, no living being for a subject request, the story of a
    shot, or any synthesis answer."""
    schema = body.get("response_format", {}).get("json_schema", {}).get("schema", {})
    if schema.get("properties") and all(re.fullmatch(r"t\d+", k) for k in schema["properties"]):
        return _translation_answer(body)
    if schema.get("required") == ["beings"]:
        return {"beings": []}
    if "main_action" in schema.get("properties", {}):
        return SHOT_ANSWER
    if (
        "logline" in schema.get("properties", {})
        or "texts" in schema.get("properties", {})
        or (
            "summary" in schema.get("properties", {})
            and "caption" not in schema.get("properties", {})
        )
    ):
        answer: dict[str, Any] = answer_for_schema(schema)
        return answer
    return FRAME_ANSWER


STREAMED_ANSWER = "D'après les passages, une femme verse du riz dans une casserole [1]."


class StreamedBody(httpx.AsyncByteStream):
    """Server-sent chunks of a streamed answer, one every ``delay`` seconds; notes whether the
    client closed it before the end (stop button, page closed)."""

    def __init__(self, fake: FakeLmStudio, chunks: list[bytes], delay: float) -> None:
        self._fake = fake
        self._chunks = chunks
        self._delay = delay
        self._done = False

    async def __aiter__(self) -> Any:
        for chunk in self._chunks:
            if self._delay:
                await anyio.sleep(self._delay)
            yield chunk
        self._done = True

    async def aclose(self) -> None:
        if not self._done:
            self._fake.stream_closed = True


def sse_chunks(
    pieces: list[str],
    *,
    model: str,
    finish_reason: str | None = "stop",
    usage: dict[str, Any] | None = None,
    error: str | None = None,
) -> list[bytes]:
    """What LM Studio sends for ``stream: true`` (checked against the real server)."""

    def line(payload: dict[str, Any]) -> bytes:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()

    head = {"id": "chatcmpl-fake", "object": "chat.completion.chunk", "model": model}
    out = [
        line({**head, "choices": [{"index": 0, "delta": {"content": piece},
                                   "finish_reason": None}]})
        for piece in pieces
    ]  # fmt: skip
    if error is not None:
        return [*out, line({"error": {"message": error}})]
    out.append(line({**head, "choices": [{"index": 0, "delta": {},
                                          "finish_reason": finish_reason}]}))  # fmt: skip
    out.append(line({**head, "choices": [], "usage": usage or {
        "prompt_tokens": 1500, "completion_tokens": 40, "total_tokens": 1540,
        "completion_tokens_details": {"reasoning_tokens": 0}}}))  # fmt: skip
    return [*out, b"data: [DONE]\n\n"]


def pieces_of(text: str, size: int = 7) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


class FakeLmStudio:
    """Records chat requests; answers can be customised per test. A ``stream: true`` request
    gets ``stream_text`` in small pieces (``sse_chunks``)."""

    def __init__(self) -> None:
        self.chat_requests: list[dict[str, Any]] = []
        self.requested_paths: list[str] = []
        self.answers: list[str] = []
        self.status_code = 200
        self.down = False
        self.replies: list[httpx.Response] = []  # served first, one per chat request
        self.finish_reason: str | None = None
        self.reasoning_tokens: int | None = None
        self.models_payload: dict[str, Any] = MODELS_PAYLOAD
        self.stream_text = STREAMED_ANSWER
        self.stream_delay = 0.0  # seconds between two chunks
        self.stream_error: str | None = None  # sent instead of the end of the answer
        self.stream_closed = False  # the client closed the stream before its end

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requested_paths.append(request.url.path)
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json=self.models_payload)
        if request.url.path == "/v1/chat/completions":
            body = json.loads(request.content)
            self.chat_requests.append(body)
            if self.replies:
                return self.replies.pop(0)
            if self.status_code != 200:
                return httpx.Response(self.status_code, text="failed to process mtmd chunk")
            if body.get("stream"):
                chunks = sse_chunks(
                    pieces_of(self.stream_text), model=body["model"],
                    finish_reason=self.finish_reason or "stop", error=self.stream_error,
                )  # fmt: skip
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    stream=StreamedBody(self, chunks, self.stream_delay),
                )
            content = self.answers.pop(0) if self.answers else json.dumps(_default_answer(body))
            return httpx.Response(
                200,
                json={
                    "model": body["model"],
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": self.finish_reason,
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 700,
                        "completion_tokens": 300,
                        "completion_tokens_details": {"reasoning_tokens": self.reasoning_tokens},
                    },
                },
            )
        return httpx.Response(404, text="not found")

    def client(self) -> LmStudioClient:
        return LmStudioClient("http://lmstudio.test", transport=httpx.MockTransport(self.handler))


@pytest.fixture
def fake_lmstudio() -> FakeLmStudio:
    return FakeLmStudio()


@pytest.fixture
def ffprobe_json() -> Callable[[str], dict[str, Any]]:
    def load(name: str) -> dict[str, Any]:
        data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        return data

    return load
