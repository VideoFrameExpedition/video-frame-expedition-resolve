"""The model bench: vision models of LM Studio compared on frames of the library."""

from __future__ import annotations

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from vfe_vision.api.deps import Container
from vfe_vision.api.schemas import ApiModel, media_url
from vfe_vision.domain.bench import (
    DEFAULT_IMAGES,
    MAX_MODELS,
    MAX_NOTE,
    MAX_RATING,
    BenchPreviousModel,
)
from vfe_vision.services import bench
from vfe_vision.services.bench import BenchFrameView, BenchRunSummary, BenchRunView

router = APIRouter(prefix="/bench", tags=["bench"])


class BenchRequest(BaseModel):
    models: list[str] = Field(
        min_length=1, max_length=MAX_MODELS, description="LM Studio keys of the models to test."
    )
    images: int = Field(
        default=DEFAULT_IMAGES, ge=1, le=200,
        description="Number of library images shown to each model.",
    )  # fmt: skip


class BenchRatingRequest(BaseModel):
    keyframe_id: str
    model: str = Field(description="LM Studio key of the model rated.")
    rating: int | None = Field(
        ge=0, le=MAX_RATING,
        description="0 wrong · 1 approximate · 2 nearly right · 3 right; null: remove the rating.",
    )  # fmt: skip


class BenchNoteRequest(BaseModel):
    note: str | None = Field(
        max_length=MAX_NOTE,
        description="What the user says about this test, to find it again; null: erase it.",
    )


class BenchFrameOut(BenchFrameView):
    image_url: str
    thumb_url: str


class BenchRunOut(BenchRunSummary, ApiModel):
    """A test: what each model measured (``model_runs``), what it answered for each image
    (``frames``) and the ratings given blind (``ratings``: image → model → 0 to 3)."""

    context_length: int
    parallel: int
    frames: list[BenchFrameOut]
    ratings: dict[str, dict[str, int]]
    previous: list[BenchPreviousModel]
    progress: float
    message: str | None

    @classmethod
    def of(cls, view: BenchRunView) -> BenchRunOut:
        return cls(
            **view.model_dump(exclude={"frames"}),
            frames=[
                BenchFrameOut(
                    **frame.model_dump(),
                    image_url=media_url(frame.image_path) or "",
                    thumb_url=media_url(frame.thumb_path) or "",
                )
                for frame in view.frames
            ],
        )


@router.get("")
async def bench_overview(c: Container) -> bench.BenchOverview:
    """The LM Studio vision models that can be tested, the graphics card's memory, and the test
    in progress if there is one."""
    return await bench.overview(c)


@router.get("/runs")
def list_runs(c: Container) -> list[BenchRunSummary]:
    """The history: the tests already run, from the most recent to the oldest, each with what
    its models measured. Two tests with the same ``image_set`` were run on the same images."""
    return bench.list_runs(c)


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
async def start_run(c: Container, body: BenchRequest) -> BenchRunOut:
    """Start a test: each model is loaded alone in LM Studio, queried as the analyses do on the
    same library images, then unloaded; the model loaded before the test is loaded again at the
    end. The analyses wait during the test. 409 if a test is already in progress or if the
    library has no image; 503 if LM Studio does not answer."""
    return BenchRunOut.of(await bench.start(c, body.models, body.images))


@router.get("/runs/{run_id}")
def get_run(c: Container, run_id: str) -> BenchRunOut:
    return BenchRunOut.of(bench.get_run(c, run_id))


@router.patch("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def annotate_run(c: Container, run_id: str, body: BenchNoteRequest) -> None:
    """Write (or erase) a test's note, shown in the history."""
    bench.annotate(c, run_id, body.note)


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_run(c: Container, run_id: str) -> None:
    """Delete a finished test (409 if it is in progress)."""
    bench.delete_run(c, run_id)


@router.put("/runs/{run_id}/ratings", status_code=status.HTTP_204_NO_CONTENT)
def rate(c: Container, run_id: str, body: BenchRatingRequest) -> None:
    """Rate a model's description of an image, blind."""
    bench.rate(c, run_id, body.keyframe_id, body.model, body.rating)
