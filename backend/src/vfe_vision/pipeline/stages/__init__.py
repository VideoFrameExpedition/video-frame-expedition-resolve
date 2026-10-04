"""Analysis stages, in their default execution order."""

from __future__ import annotations

from vfe_vision.pipeline.registry import StageRegistry
from vfe_vision.pipeline.stages.analysis_pass import AnalysisPassStage
from vfe_vision.pipeline.stages.audio_events import AudioEventsStage
from vfe_vision.pipeline.stages.audio_levels import AudioLevelsStage
from vfe_vision.pipeline.stages.detections import DetectionsStage
from vfe_vision.pipeline.stages.grounding import GroundingStage
from vfe_vision.pipeline.stages.keyframes import KeyframesStage
from vfe_vision.pipeline.stages.metadata import MetadataStage
from vfe_vision.pipeline.stages.ocr import OcrStage
from vfe_vision.pipeline.stages.place import PlaceStage
from vfe_vision.pipeline.stages.probe import ProbeStage
from vfe_vision.pipeline.stages.proxy import ProxyStage
from vfe_vision.pipeline.stages.search_index import IndexStage
from vfe_vision.pipeline.stages.sun import SunStage
from vfe_vision.pipeline.stages.synthesis import SynthesisStage
from vfe_vision.pipeline.stages.technical import TechnicalStage
from vfe_vision.pipeline.stages.transcript import TranscriptStage
from vfe_vision.pipeline.stages.translation import TranslationStage
from vfe_vision.pipeline.stages.vision_frames import VisionFramesStage
from vfe_vision.pipeline.stages.vision_shots import VisionShotsStage
from vfe_vision.pipeline.stages.weather import WeatherStage


def default_registry() -> StageRegistry:
    """Registration order is the execution order among independent stages: cheap local
    analyses first, then the vision model, and the transcription last (minutes of CPU per
    video: the descriptions do not wait for it)."""
    return StageRegistry(
        [
            ProbeStage(),
            MetadataStage(),
            PlaceStage(),  # online lookups early: they wait on the network, not the CPU
            WeatherStage(),
            SunStage(),
            AnalysisPassStage(),
            KeyframesStage(),
            TechnicalStage(),
            AudioLevelsStage(),
            AudioEventsStage(),  # sounds and instruments (YAMNet, seconds per video)
            OcrStage(),
            DetectionsStage(),  # people, animals and faces (CPU, a fraction of a second each)
            ProxyStage(),  # viewing copy for formats the browser cannot play (APV…)
            VisionFramesStage(),
            GroundingStage(),  # every living being boxed by the vision model
            TranscriptStage(),
            VisionShotsStage(),  # after the transcript: long shots are cut in speech pauses
            SynthesisStage(),  # it summarises everything above
            TranslationStage(),  # every text above in French and in English
            IndexStage(),  # last: the search index reads everything, the synthesis too
        ]
    )
