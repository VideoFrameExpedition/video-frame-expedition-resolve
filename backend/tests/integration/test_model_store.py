"""Model store: verified downloads, resumed files, files taken out of an archive (cuBLAS wheel).

Served by an ``httpx.MockTransport``: no network. The folder only appears once every file
matches its size and sha256.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from vfe_vision.adapters.models.catalog import CATALOG, ModelFile, ModelSpec, spec
from vfe_vision.adapters.models.store import MANIFEST, ModelStore
from vfe_vision.core.config import Settings
from vfe_vision.core.errors import VfeError
from vfe_vision.db.session import Database

BASE = "https://models.example"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _server(files: dict[str, bytes], log: list[str] | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if log is not None:
            log.append(request.url.path)
        body = files.get(request.url.path)
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, content=body)

    return httpx.MockTransport(handler)


def _wheel(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, data in members.items():
            bundle.writestr(name, data)
    return buffer.getvalue()


def test_plain_files_are_checked_and_recorded(tmp_path: Path) -> None:
    model, labels = b"onnx" * 1000, b"index,mid,display_name\n"
    item = ModelSpec(
        id="sounds/test", kind="sounds", label="Test", licence="MIT", source=BASE,
        files=(
            ModelFile("model.onnx", f"{BASE}/m.onnx", len(model), _sha(model)),
            ModelFile("labels.csv", f"{BASE}/l.csv", len(labels), _sha(labels)),
        ),
    )  # fmt: skip
    store = ModelStore(tmp_path)
    assert store.installed(item) is None
    seen: list[tuple[str, int, int]] = []
    folder = store.download(
        item,
        transport=_server({"/m.onnx": model, "/l.csv": labels}),
        progress=lambda name, done, total: seen.append((name, done, total)),
    )
    assert store.installed(item) == folder == tmp_path / "sounds" / "test"
    assert (folder / "model.onnx").read_bytes() == model
    assert seen[-1][1:] == (item.size, item.size)
    assert store.identity(item) == f"sounds/test@{_sha(model)[:8]}"
    assert store.verify(item) == []
    manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    assert [f["name"] for f in manifest["files"]] == ["model.onnx", "labels.csv"]


def test_a_wrong_file_is_refused_and_nothing_is_installed(tmp_path: Path) -> None:
    good = b"good"
    item = ModelSpec(
        id="x/bad", kind="sounds", label="Bad", licence="MIT", source=BASE,
        files=(ModelFile("f.bin", f"{BASE}/f", len(good), _sha(good)),),
    )  # fmt: skip
    store = ModelStore(tmp_path)
    with pytest.raises(VfeError, match="invalide"):
        store.download(item, transport=_server({"/f": b"evil"}))
    assert store.installed(item) is None
    assert not (tmp_path / "x" / "bad").exists()


def test_files_are_taken_out_of_an_archive(tmp_path: Path) -> None:
    lt, blas = b"L" * 5000, b"B" * 3000
    wheel = _wheel({"nvidia/cublas/bin/a.dll": lt, "nvidia/cublas/bin/b.dll": blas, "x.txt": b""})

    def member(name: str, data: bytes) -> ModelFile:
        return ModelFile(
            name, f"{BASE}/pkg.whl", len(data), _sha(data), member=f"nvidia/cublas/bin/{name}",
            archive_size=len(wheel), archive_sha256=_sha(wheel),
        )  # fmt: skip

    item = ModelSpec(
        id="runtime/test", kind="runtime", label="Runtime", licence="EULA", source=BASE,
        files=(member("a.dll", lt), member("b.dll", blas)),
    )  # fmt: skip
    assert item.download_size == len(wheel)  # the archive once, not the extracted size
    requests: list[str] = []
    store = ModelStore(tmp_path)
    folder = store.download(item, transport=_server({"/pkg.whl": wheel}, requests))
    assert requests == ["/pkg.whl"]
    assert store.installed(item) == folder
    assert (folder / "a.dll").read_bytes() == lt
    assert sorted(p.name for p in folder.iterdir()) == ["MODEL.json", "a.dll", "b.dll"]
    assert store.verify(item) == []


def test_an_archive_or_member_that_does_not_match_is_refused(tmp_path: Path) -> None:
    wheel = _wheel({"bin/a.dll": b"tampered"})
    good = b"original"
    base = ModelFile(
        "a.dll", f"{BASE}/pkg.whl", len(good), _sha(good), member="bin/a.dll",
        archive_size=len(wheel), archive_sha256=_sha(wheel),
    )  # fmt: skip
    item = ModelSpec(id="runtime/t", kind="runtime", label="R", licence="E", source=BASE,
                     files=(base,))  # fmt: skip
    store = ModelStore(tmp_path)
    with pytest.raises(VfeError, match="extrait invalide"):
        store.download(item, transport=_server({"/pkg.whl": wheel}))
    assert store.installed(item) is None
    other = _wheel({"bin/a.dll": good, "padding": b"x"})
    with pytest.raises(VfeError, match="invalide"):  # the archive's own sha256 differs
        store.download(item, transport=_server({"/pkg.whl": other}))


def test_catalogue_entries_are_consistent() -> None:
    assert len({item.id for item in CATALOG}) == len(CATALOG)
    for item in CATALOG:
        assert len({f.name for f in item.files}) == len(item.files)
        for f in item.files:
            assert len(f.sha256) == 64
            assert f.url.startswith("https://")
            if f.member is not None:
                assert f.archive_size
                assert f.archive_sha256
                assert len(f.archive_sha256) == 64
    runtime = spec("runtime/cublas-12.9")
    assert [f.name for f in runtime.files] == ["cublasLt64_12.dll", "cublas64_12.dll"]
    assert runtime.download_size == 553_162_896


def test_doctor_calls_ced_optional_and_update_adds_it(
    settings: Settings, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vfe_vision.adapters.ffmpeg.tools import Ffmpeg
    from vfe_vision.adapters.lmstudio.client import LmStudioClient
    from vfe_vision.services import system
    from vfe_vision.services.container import AppContainer
    from vfe_vision.storage.artifacts import ArtifactStore

    c = AppContainer(
        settings=settings,
        db=db,
        artifacts=ArtifactStore(settings.artifacts_dir),
        ffmpeg=Ffmpeg(settings.ffmpeg_path, settings.ffprobe_path),
        lmstudio=LmStudioClient("http://lmstudio.test"),
    )
    nothing = system._models(c)
    assert "vfe models sounds" in (nothing.hint or "")
    assert "vfe models yamnet" not in (nothing.hint or "")  # sounds installs YAMNet too

    def installed(self: ModelStore, item: ModelSpec) -> Path | None:
        return None if item.id == "sounds/ced-small" else settings.models_dir

    monkeypatch.setattr(ModelStore, "installed", installed)
    hint = system._models(c).hint or ""
    assert "Mettre à jour" in hint
    assert "Compléter" not in hint
    assert "sautées" not in hint
