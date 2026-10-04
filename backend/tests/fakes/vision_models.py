"""LM Studio fakes serving vision models other than Qwen3-VL (calibration)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from tests.conftest import MODELS_PAYLOAD, FakeLmStudio
from vfe_vision.adapters.imaging import draw_probe_scene
from vfe_vision.domain.vision_profile import SCENES


class OtherModel(FakeLmStudio):
    """LM Studio with a vision model whose box convention is unknown."""

    def handler(self, request: Any) -> Any:

        if request.url.path == "/api/v1/models":
            payload = json.loads(json.dumps(MODELS_PAYLOAD))
            payload["models"][0]["key"] = "google/gemma-4-12b"
            payload["models"][0]["architecture"] = "gemma4"
            payload["models"][0]["loaded_instances"][0]["id"] = "google/gemma-4-12b"
            return httpx.Response(200, json=payload)
        return super().handler(request)


def is_probe(body: dict[str, Any]) -> bool:
    return str(body["messages"][0]["content"]).startswith("You locate objects in images")


class GemmaLike(OtherModel):
    """A vision model writing [y1, x1, y2, x2] in 0-1000, whatever the field says."""

    y_first = True

    def handler(self, request: Any) -> Any:

        if request.url.path != "/v1/chat/completions":
            return super().handler(request)
        body = json.loads(request.content)
        self.chat_requests.append(body)
        if is_probe(body):
            text = body["messages"][1]["content"][0]["text"]
            field = "bbox_2d" if "bbox_2d:" in text else "box_2d"
            portrait = "person silhouette" in text
            scene = SCENES[1] if portrait else SCENES[0]
            _, _, truth = draw_probe_scene(scene)
            order = (1, 0, 3, 2) if self.y_first else (0, 1, 2, 3)
            answer: dict[str, Any] = {
                "objects": [
                    {"label": o.name, field: [round(t[i] * 1000) for i in order]}
                    for o, t in zip(scene.objects, truth, strict=True)
                ]
            }
        else:
            answer = {"beings": [{"label": "chat", "category": "mammal", "main": True,
                                  "box_2d": [100, 200, 500, 600]}]}  # fmt: skip
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "choices": [{"message": {"content": json.dumps(answer)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 800, "completion_tokens": 170},
            },
        )


class XFirstOther(GemmaLike):
    """Another model writing [x1, y1, x2, y2] in 0-1000, like Qwen3-VL, without its prior."""

    y_first = False


class Reasoner(OtherModel):
    """A model that keeps reasoning despite ``reasoning_effort: none``: every answer is cut at
    ``max_tokens``, all of them thinking tokens."""

    def handler(self, request: Any) -> Any:

        if request.url.path != "/v1/chat/completions":
            return super().handler(request)
        body = json.loads(request.content)
        self.chat_requests.append(body)
        spent = int(body["max_tokens"])
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                "usage": {
                    "prompt_tokens": 800,
                    "completion_tokens": spent,
                    "completion_tokens_details": {"reasoning_tokens": spent},
                },
            },
        )
