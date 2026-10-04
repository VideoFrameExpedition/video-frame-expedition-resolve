"""User preferences that influence analysis results (edited in the UI, stored in the DB)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from vfe_vision.domain.path_map import FolderPair


class ResolveFolder(BaseModel):
    """A folder of this computer and the same folder as DaVinci Resolve sees it."""

    model_config = ConfigDict(extra="forbid")

    here: str = Field(min_length=1, max_length=1024, description="On this computer: D:\\Rushs.")
    there: str = Field(
        min_length=1, max_length=1024, description="As Resolve sees it: /Volumes/Rushs."
    )


class AnalysisPreferences(BaseModel):
    model_config = ConfigDict(extra="ignore")

    language: str = Field(default="fr", pattern=r"^[a-z]{2}$")
    analysis_focus: str | None = Field(
        default=None, description="Global analysis focus, overridden per folder or per job."
    )

    # LM Studio — the loaded instance is used as-is
    vision_model: str | None = Field(
        default=None, description="Model key or loaded instance id; None = first loaded VLM."
    )
    vision_image_long_side: int = Field(default=1024, ge=384, le=1536)
    vision_max_tokens: int = Field(default=900, ge=200, le=4000)

    # Keyframes
    keyframe_interval_s: float = Field(default=2.0, gt=0.2, le=60)
    scene_threshold: float = Field(default=0.3, gt=0, lt=1)
    max_keyframes: int = Field(default=150, ge=5, le=1000)
    dedup_max_distance: int = Field(default=6, ge=0, le=20)

    # Speech: Whisper on the CPU, in a child process
    transcription: bool = Field(
        default=True, description="Transcribe speech (except videos set to « never »)."
    )
    whisper_model: Literal["whisper/large-v3-turbo", "whisper/small", "whisper/tiny"] = Field(
        default="whisper/large-v3-turbo",
        description="Transcription model (turbo: accurate; small/tiny: faster).",
    )

    # Hardware: never at the vision model's expense, and not part of any result
    gpu_decode: bool = Field(
        default=True,
        description="Decode videos on the GPU when the memory left by the vision model allows "
        "it (else on the CPU).",
    )
    gpu_transcription: bool = Field(
        default=False,
        description="Transcribe on the GPU when the vision model leaves enough memory "
        "(about 2.6 GB free for turbo); needs « vfe models cuda-runtime ».",
    )

    # Online services
    online_services: bool = True
    nominatim_email: str | None = None

    # The analysis file next to each video: written after every analysis
    sidecar_files: bool = Field(
        default=True,
        description="Write the analysis files next to the videos (<name>_FR.txt and "
        "<name>_EN.txt, without images) after each analysis. They are read first when a "
        "video has no analysis yet.",
    )

    # DaVinci Resolve on another computer: read there, the rushes through a share
    resolve_host: str | None = Field(
        default=None,
        max_length=253,
        pattern=r"^[A-Za-z0-9._:\-\[\]]+$",
        description="Computer where DaVinci Resolve runs (name or Tailscale address, local "
        "network); empty: this computer. Resolve there must accept « Network » scripting.",
    )
    resolve_folders: list[ResolveFolder] = Field(
        default_factory=list,
        max_length=20,
        description="The same folders as seen from this computer and from Resolve's.",
    )

    # MCP: a transcript or on-screen text could ask Claude to analyse other folders
    mcp_add_folders: bool = Field(
        default=False,
        description="Allow Claude (MCP analyze_folder, import_resolve_timeline) to add "
        "a new folder, or the videos of a Resolve timeline stored outside the declared "
        "folders; without this, it only analyses the folders already declared.",
    )
    # Resolve tools for the assistant: read a timeline, build new ones, markers,
    # reframing plans. Off by default: they change the project open in Resolve.
    mcp_resolve_tools: bool = Field(
        default=False,
        description="Enhanced DaVinci Resolve tools for the assistant (MCP read_timeline, "
        "build_timeline, apply_markers, plan_reframe): it reads timelines, creates new "
        "ones (« … - vfe vN », never an existing timeline) and puts markers, through "
        "the application's fixed scripts.",
    )


def folder_pairs(prefs: AnalysisPreferences) -> list[FolderPair]:
    """The folder pairs of the preferences, for ``domain.path_map``."""
    return [FolderPair(here=f.here, there=f.there) for f in prefs.resolve_folders]
