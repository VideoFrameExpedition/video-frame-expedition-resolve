"""A fake DaVinci Resolve object model, enough for the fixed script.

It behaves as Resolve 21.1 was measured to: one marker per frame (``AddMarker`` answers False on
a taken frame), ``DeleteMarkerByCustomData`` deletes the first match only, ``GetClipProperty()``
without a key returns every property, ``SetMetadata`` refuses unknown fields as a whole.
"""

from __future__ import annotations

import builtins
from dataclasses import dataclass, field
from typing import Any

from vfe_vision.adapters.resolve.script import render_script

METADATA_FIELDS = frozenset({"Keywords", "Description", "Comments", "Scene", "Shot"})


@dataclass
class FakeClip:
    path: str
    fps: float = 25.0
    frames: int = 3000
    name: str = ""
    markers: dict[int, dict[str, Any]] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)
    third_party: dict[str, str] = field(default_factory=dict)
    refuse: frozenset[str] = frozenset()  # metadata fields this Resolve refuses

    def GetName(self) -> str:  # noqa: N802 - Resolve's API
        return self.name or self.path.replace("\\", "/").rsplit("/", 1)[-1]

    def GetClipProperty(self, key: str | None = None) -> Any:  # noqa: N802
        properties = {"File Path": self.path, "FPS": self.fps, "Frames": str(self.frames)}
        return properties if key is None else properties.get(key, "")

    def GetMarkers(self) -> dict[int, dict[str, Any]]:  # noqa: N802
        return {frame: dict(info) for frame, info in sorted(self.markers.items())}

    def AddMarker(  # noqa: N802, PLR0917 - Resolve's signature
        self, frame: float, color: str, name: str, note: str, duration: float, custom: str = ""
    ) -> bool:
        """Frames must be whole (the script rounds them itself); one marker per frame."""
        if not isinstance(frame, int) or not isinstance(duration, int) or frame in self.markers:
            return False
        self.markers[frame] = {"color": color, "name": name, "note": note,
                               "duration": duration, "customData": custom}  # fmt: skip
        return True

    def DeleteMarkerByCustomData(self, custom: str) -> bool:  # noqa: N802
        for frame, info in sorted(self.markers.items()):
            if info["customData"] == custom:
                del self.markers[frame]
                return True
        return False

    def DeleteMarkerAtFrame(self, frame: int) -> bool:  # noqa: N802
        return self.markers.pop(frame, None) is not None

    def GetMetadata(self, key: str | None = None) -> Any:  # noqa: N802
        return dict(self.metadata) if key is None else self.metadata.get(key, "")

    def SetMetadata(self, values: dict[str, str]) -> bool:  # noqa: N802
        if any(k not in METADATA_FIELDS or k in self.refuse for k in values):
            return False
        self.metadata.update(values)
        return True

    def GetThirdPartyMetadata(self, key: str | None = None) -> Any:  # noqa: N802
        return dict(self.third_party) if key is None else self.third_party.get(key, "")

    def SetThirdPartyMetadata(self, values: dict[str, str]) -> bool:  # noqa: N802
        self.third_party.update({k: str(v) for k, v in values.items()})
        return True


@dataclass
class FakeFolder:
    clips: list[FakeClip] = field(default_factory=list)
    folders: list[FakeFolder] = field(default_factory=list)

    def GetClipList(self) -> list[FakeClip]:  # noqa: N802
        return list(self.clips)

    def GetSubFolderList(self) -> list[FakeFolder]:  # noqa: N802
        return list(self.folders)


@dataclass
class FakeProject:
    root: FakeFolder

    def GetMediaPool(self) -> FakeProject:  # noqa: N802 - the media pool is the project here
        return self

    def GetRootFolder(self) -> FakeFolder:  # noqa: N802
        return self.root


@dataclass
class FakeResolve:
    project: FakeProject | None

    def GetProjectManager(self) -> FakeResolve:  # noqa: N802
        return self

    def GetCurrentProject(self) -> FakeProject | None:  # noqa: N802
        return self.project


def run_script(script: str, resolve: FakeResolve, *, as_menu: bool = False) -> dict[str, Any]:
    """Execute the script as Resolve would (``run_script`` gives ``resolve`` and ``project``,
    the Scripts menu ``resolve`` only) and return its namespace. Like the real ``run_script``
    sandbox (Resolve 21.1), the builtins have no ``globals``."""
    sandbox = {k: v for k, v in vars(builtins).items() if k != "globals"}
    namespace: dict[str, Any] = {
        "__name__": "__main__",
        "__builtins__": sandbox,
        "resolve": resolve,
    }
    if not as_menu:
        namespace["project"] = resolve.project
    exec(compile(script, "apply_payload_v1.py", "exec"), namespace)  # noqa: S102 - the shipped script
    return namespace


def apply(payload: dict[str, Any], resolve: FakeResolve) -> dict[str, Any]:
    """Render the script with ``payload`` and run it; return its ``result``."""
    result: dict[str, Any] = run_script(render_script(payload), resolve)["result"]
    return result
