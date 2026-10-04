"""A fake LM Studio holding several vision models that can be loaded and unloaded, and a video
card whose memory follows what is loaded (the model bench)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from tests.conftest import FRAME_ANSWER, FakeLmStudio
from tests.fakes.vision_models import is_probe
from vfe_vision.adapters.imaging import draw_probe_scene
from vfe_vision.domain.vision_profile import SCENES
from vfe_vision.ports.gpu import VramUsage

SMALL, LARGE, GEMMA, TEXT_ONLY = (
    "qwen/qwen3-vl-4b",
    "qwen/qwen3-vl-8b",
    "google/gemma-4-12b",
    "acme/text-only",
)
CATALOGUE: list[dict[str, Any]] = [
    {
        "type": "llm", "key": SMALL, "display_name": "Qwen3 VL 4B", "publisher": "qwen",
        "architecture": "qwen3vl", "params_string": "4B", "size_bytes": 3_300_000_000,
        "quantization": {"name": "Q4_K_M"}, "max_context_length": 262144,
        "capabilities": {"vision": True},
    },
    {
        "type": "llm", "key": LARGE, "display_name": "Qwen3 VL 8B", "publisher": "qwen",
        "architecture": "qwen3vl", "params_string": "8B", "size_bytes": 9_870_000_000,
        "quantization": {"name": "Q8_0"}, "max_context_length": 262144,
        "capabilities": {"vision": True},
    },
    {
        "type": "llm", "key": GEMMA, "display_name": "Gemma 4 12B", "publisher": "google",
        "architecture": "gemma4", "params_string": "12B", "size_bytes": 7_150_000_000,
        "quantization": {"name": "Q4_0"}, "max_context_length": 8192,
        "capabilities": {"vision": True, "reasoning": {"allowed_options": ["off", "on"]}},
    },
    {
        "type": "llm", "key": TEXT_ONLY, "display_name": "Text Only", "size_bytes": 2_000_000_000,
        "capabilities": {"vision": False},
    },
    {"type": "embedding", "key": "text-embedding-nomic", "display_name": "Nomic Embed"},
]  # fmt: skip
CAT = [0.2, 0.3, 0.7, 0.9]  # where the cat is in every frame, [x1, y1, x2, y2] in 0–1


class BenchLmStudio(FakeLmStudio):
    """Qwen models write boxes x first, Gemma y first; every model describes a frame in French
    and finds one cat. ``loaded`` maps the loaded instances to their settings."""

    def __init__(self, loaded: tuple[str, ...] = (LARGE,)) -> None:
        super().__init__()
        self.loaded: dict[str, dict[str, Any]] = {
            key: {"context_length": 10496, "parallel": 4} for key in loaded
        }
        self.loads: list[dict[str, Any]] = []
        self.unloads: list[str] = []
        self.refused: set[str] = set()  # models LM Studio cannot load
        self.english: set[str] = set()  # models answering in English whatever is asked
        self.visible_text = "RIZ BASMATI"

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        if path == "/api/v1/models":
            models = [
                {**model, "loaded_instances": [
                    {"id": key, "config": config}
                    for key, config in self.loaded.items() if key == model["key"]
                ]}
                for model in CATALOGUE
            ]  # fmt: skip
            return httpx.Response(200, json={"models": models})
        if path == "/api/v1/models/load":
            return self._load(json.loads(request.content))
        if path == "/api/v1/models/unload":
            instance = json.loads(request.content)["instance_id"]
            if instance not in self.loaded:
                message = f"Model with instance identifier '{instance}' is not loaded."
                return httpx.Response(404, json={"error": {"message": message}})
            self.unloads.append(instance)
            del self.loaded[instance]
            return httpx.Response(200, json={"instance_id": instance})
        if path == "/v1/chat/completions":
            body = json.loads(request.content)
            self.chat_requests.append(body)
            return self._chat(body)
        return httpx.Response(404, text="not found")

    def _load(self, body: dict[str, Any]) -> httpx.Response:
        self.loads.append(body)
        key = body["model"]
        if key in self.refused:
            error = {"type": "load_failed", "message": "Not enough memory to load the model."}
            return httpx.Response(400, json={"error": error})
        config = {"context_length": body.get("context_length"), "parallel": body.get("parallel")}
        self.loaded[key] = config
        return httpx.Response(
            200,
            json={"type": "llm", "instance_id": key, "load_time_seconds": 1.5,
                  "status": "loaded", "load_config": config},
        )  # fmt: skip

    def _chat(self, body: dict[str, Any]) -> httpx.Response:
        model = body["model"]
        y_first = model == GEMMA
        schema = body["response_format"]["json_schema"]["schema"]
        answer: dict[str, Any]
        if is_probe(body):
            text = body["messages"][1]["content"][0]["text"]
            field = "bbox_2d" if "bbox_2d:" in text else "box_2d"
            scene = SCENES[1] if "person silhouette" in text else SCENES[0]
            _, _, truth = draw_probe_scene(scene)
            order = (1, 0, 3, 2) if y_first else (0, 1, 2, 3)
            answer = {
                "objects": [
                    {"label": o.name, field: [round(t[i] * 1000) for i in order]}
                    for o, t in zip(scene.objects, truth, strict=True)
                ]
            }
        elif schema.get("required") == ["beings"]:
            x1, y1, x2, y2 = (round(v * 1000) for v in CAT)
            box = {"box_2d": [y1, x1, y2, x2]} if y_first else {"bbox_2d": [x1, y1, x2, y2]}
            answer = {"beings": [{"label": "chat", "category": "mammal", "main": True, **box}]}
        else:
            answer = {
                **FRAME_ANSWER,
                "caption": "Un chat noir dort sur le canapé.",
                "description": "Le chat est roulé en boule dans le salon, avec une couverture.",
                "visible_text": self.visible_text,
            }
            if model in self.english:
                answer["description"] = (
                    "The cat is curled up on the sofa with a blanket and it is asleep."
                )
        return httpx.Response(
            200,
            json={
                "model": model,
                "choices": [{"message": {"content": json.dumps(answer)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 800, "completion_tokens": 200,
                          "completion_tokens_details": {"reasoning_tokens": 0}},
            },
        )  # fmt: skip


class FakeVram:
    """A 12 GiB card: 1 GiB for the screen, plus the file of every loaded model."""

    def __init__(self, lm: BenchLmStudio) -> None:
        self.lm = lm

    def usage(self) -> VramUsage:
        sizes = {model["key"]: int(model.get("size_bytes") or 0) for model in CATALOGUE}
        used = 1024 + sum(sizes[key] // (1024 * 1024) for key in self.lm.loaded)
        return VramUsage(used=used, free=12288 - used, total=12288)
