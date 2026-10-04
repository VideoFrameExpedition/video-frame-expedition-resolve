"""Artifact store: keyframes, thumbnails and exports live under the data directory.

Paths stored in the database are POSIX paths relative to the store root, so the data directory
can be moved without rewriting rows. Nothing of the store goes to the video folders: the
only file written there is each video's analysis file (``pipeline.sidecar``).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from vfe_vision.core.atomic_io import atomic_write_bytes
from vfe_vision.core.errors import PathNotAllowedError
from vfe_vision.core.paths import joined_inside


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def video_dir(self, video_id: str) -> Path:
        # Two-level fan-out keeps directories small for large libraries.
        return self.root / video_id[-2:] / video_id

    def subdir(self, video_id: str, name: str, *, reset: bool = False) -> Path:
        directory = self.video_dir(video_id) / name
        if reset and directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def prune(self, video_id: str, name: str, *, keep: str) -> None:
        """Remove everything under ``<video>/<name>/`` except the ``keep`` child."""
        directory = self.video_dir(video_id) / name
        if not directory.is_dir():
            return
        for child in directory.iterdir():
            if child.name == keep:
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)

    def rel(self, path: Path) -> str:
        """Store-relative POSIX path of an absolute path inside the store."""
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError as exc:
            raise PathNotAllowedError(f"{path} n'est pas dans le dossier des artefacts") from exc

    def resolve(self, rel_path: str) -> Path:
        """Absolute path of a stored artifact; rejects traversal outside the store, on the text
        before any disk access (a network path is never looked at)."""
        joined = joined_inside(self.root, rel_path)
        if joined is None:
            raise PathNotAllowedError(f"Chemin d'artefact invalide : {rel_path}")
        candidate = joined.resolve()
        if not candidate.is_relative_to(self.root):
            raise PathNotAllowedError(f"Chemin d'artefact invalide : {rel_path}")
        return candidate

    def write_bytes(self, rel_path: str, data: bytes) -> Path:
        target = self.resolve(rel_path)
        atomic_write_bytes(target, data)
        return target

    def delete_video(self, video_id: str) -> None:
        shutil.rmtree(self.video_dir(video_id), ignore_errors=True)

    def size_bytes(self, video_id: str | None = None) -> int:
        base = self.video_dir(video_id) if video_id else self.root
        if not base.exists():
            return 0
        return sum(p.stat().st_size for p in base.rglob("*") if p.is_file())
