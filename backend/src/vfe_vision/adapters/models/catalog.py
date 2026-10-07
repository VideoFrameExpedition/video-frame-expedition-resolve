"""Downloadable models: fixed URLs, sizes and sha256, with their licences.

Revisions are pinned by commit: a repository renamed or rewritten upstream cannot change what
is installed (the large-v3-turbo repository already moved from mobiuslabsgmbh to dropbox-dash).
"""

from __future__ import annotations

from dataclasses import dataclass

HF = "https://huggingface.co"


@dataclass(frozen=True, slots=True)
class ModelFile:
    name: str  # file name once installed
    url: str
    size: int  # of the installed file
    sha256: str
    # A file taken out of a downloaded archive (a Python wheel): its path inside, and the
    # archive's own size and sha256, checked before anything is extracted.
    member: str | None = None
    archive_size: int | None = None
    archive_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ModelSpec:
    id: str  # e.g. "whisper/large-v3-turbo": also the folder under the models directory
    kind: str  # whisper | yamnet | sounds | ocr | detector | faces | runtime | embeddings
    label: str
    licence: str
    source: str
    files: tuple[ModelFile, ...]
    # As an English terminal shows them (``vfe models``); empty: the same as above.
    label_en: str = ""
    licence_en: str = ""

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def download_size(self) -> int:
        """Bytes fetched: each archive once, plain files as they are."""
        archives = {f.url: f.archive_size or 0 for f in self.files if f.member}
        return sum(archives.values()) + sum(f.size for f in self.files if not f.member)


def _hf(repo: tuple[str, str], path: str, name: str, size: int, sha256: str) -> ModelFile:
    """A file of a Hugging Face repository at a pinned commit (``repo`` = name, revision)."""
    return ModelFile(name, f"{HF}/{repo[0]}/resolve/{repo[1]}/{path}", size, sha256)


_TURBO = ("dropbox-dash/faster-whisper-large-v3-turbo", "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf")
_SMALL = ("Systran/faster-whisper-small", "536b0662742c02347bc0e980a01041f333bce120")
_TINY = ("Systran/faster-whisper-tiny", "d90ca5fe260221311c53c58e660288d3deb8d356")
_SMALL_TOKENIZER = (2203239, "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab")
_SMALL_VOCABULARY = (459861, "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913")
_YAMNET = ("zeropointnine/yamnet-onnx", "ac2ca3bd45d12ec1f19f1144205ea529b4e9dedf")
_CED = ("mispeech/ced-small", "06bb40c5ec089e96867ebc5246be02441f4a71e4")
_AUDIOSET_CLASSES = (
    "k2-fsa/sherpa-onnx-ced-tiny-audio-tagging-2024-04-19",
    "efe3a5cfad74e56598d855d055006903adc00e38",
)
_OCR_DET = ("PaddlePaddle/PP-OCRv6_small_det_onnx", "28fe5895c24fd108c19eb3e8479f4ab385fbfc62")
_OCR_REC = ("PaddlePaddle/PP-OCRv6_small_rec_onnx", "b8f84f0b80c529de40b4fbb3544b84fa7233a513")
_DFINE_S = ("onnx-community/dfine_s_coco-ONNX", "a3cf03147a9b86c78475139115c8ac142577352d")
_YUNET = ("opencv/face_detection_yunet", "3cc26e7f1014a5ee5d74a42acee58bafc9d0a310")
_GEMMA = ("onnx-community/embeddinggemma-300m-ONNX", "5090578d9565bb06545b4552f76e6bc2c93e4a66")
_CUBLAS_WHEEL = (
    "https://files.pythonhosted.org/packages/20/e2/"
    "fc9a0e985249d873150276d5afb02e39a66817fedbf1a385724393e505ed/"
    "nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl"
)
_CUBLAS_WHEEL_SIZE = 553162896
_CUBLAS_WHEEL_SHA256 = "623f43027d40d44ceadf0043f002bd25cf353e8f13ce90b9a87057019f560661"


def _cublas(name: str, size: int, sha256: str) -> ModelFile:
    return ModelFile(
        name, _CUBLAS_WHEEL, size, sha256, member=f"nvidia/cublas/bin/{name}",
        archive_size=_CUBLAS_WHEEL_SIZE, archive_sha256=_CUBLAS_WHEEL_SHA256,
    )  # fmt: skip


CATALOG: tuple[ModelSpec, ...] = (
    ModelSpec(
        id="whisper/large-v3-turbo",
        kind="whisper",
        label="Whisper large-v3-turbo (transcription, CTranslate2)",
        licence="MIT (OpenAI Whisper, conversion CTranslate2)",
        licence_en="MIT (OpenAI Whisper, CTranslate2 conversion)",
        source=f"{HF}/{_TURBO[0]}",
        files=(
            _hf(_TURBO, "config.json", "config.json", 2263,
                "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e"),
            _hf(_TURBO, "model.bin", "model.bin", 1617884929,
                "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da"),
            _hf(_TURBO, "preprocessor_config.json", "preprocessor_config.json", 340,
                "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711"),
            _hf(_TURBO, "tokenizer.json", "tokenizer.json", 2710337,
                "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd"),
            _hf(_TURBO, "vocabulary.json", "vocabulary.json", 1068114,
                "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1"),
        ),
    ),
    ModelSpec(
        id="whisper/small",
        kind="whisper",
        label="Whisper small (transcription rapide, moins précise)",
        licence="MIT (OpenAI Whisper, conversion CTranslate2)",
        label_en="Whisper small (faster transcription, less accurate)",
        licence_en="MIT (OpenAI Whisper, CTranslate2 conversion)",
        source=f"{HF}/{_SMALL[0]}",
        files=(
            _hf(_SMALL, "config.json", "config.json", 2370,
                "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828"),
            _hf(_SMALL, "model.bin", "model.bin", 483546902,
                "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"),
            _hf(_SMALL, "tokenizer.json", "tokenizer.json", *_SMALL_TOKENIZER),
            _hf(_SMALL, "vocabulary.txt", "vocabulary.txt", *_SMALL_VOCABULARY),
        ),
    ),
    ModelSpec(
        id="whisper/tiny",
        kind="whisper",
        label="Whisper tiny (essais uniquement)",
        licence="MIT (OpenAI Whisper, conversion CTranslate2)",
        label_en="Whisper tiny (tests only)",
        licence_en="MIT (OpenAI Whisper, CTranslate2 conversion)",
        source=f"{HF}/{_TINY[0]}",
        files=(
            _hf(_TINY, "config.json", "config.json", 2249,
                "a73a28cdfe1c43ccc7202fa333d1f89c202477271407ae9a7f19afa52039cac8"),
            _hf(_TINY, "model.bin", "model.bin", 75538270,
                "dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1"),
            _hf(_TINY, "tokenizer.json", "tokenizer.json", *_SMALL_TOKENIZER),
            _hf(_TINY, "vocabulary.txt", "vocabulary.txt", *_SMALL_VOCABULARY),
        ),
    ),
    ModelSpec(
        id="yamnet",
        kind="yamnet",
        label="YAMNet (sons et instruments, 521 classes AudioSet)",
        licence="Apache-2.0 (Google YAMNet, conversion ONNX) ; ontologie AudioSet CC BY-SA 4.0",
        label_en="YAMNet (sounds and instruments, 521 AudioSet classes)",
        licence_en="Apache-2.0 (Google YAMNet, ONNX conversion); AudioSet ontology CC BY-SA 4.0",
        source=f"{HF}/{_YAMNET[0]}",
        files=(
            _hf(_YAMNET, "yamnet.onnx", "yamnet.onnx", 16093603,
                "1510041dce24a2e9e84ec546807ac408ae496da6d1ed41bc3ccba649623f8e19"),
            _hf(_YAMNET, "yamnet_class_map.csv", "yamnet_class_map.csv", 14096,
                "cdf24d193e196d9e95912a2667051ae203e92a2ba09449218ccb40ef787c6df2"),
            ModelFile(
                "ontology.json",
                "https://raw.githubusercontent.com/audioset/ontology/"
                "d417d32bf59c711abb5910fd2f76a0eb44697991/ontology.json",
                342780,
                "9c685f4403eecc3ca9be37fd7285cf212feaaea6ff7229d3e7ca89e0d1f2d15d",
            ),
        ),
    ),
    ModelSpec(
        id="sounds/ced-small",
        kind="sounds",
        label="CED-small (second avis sur les sons, 527 classes AudioSet)",
        licence="Apache-2.0 (CED, mispeech) ; liste des classes AudioSet CC BY 4.0",
        label_en="CED-small (second opinion on sounds, 527 AudioSet classes)",
        licence_en="Apache-2.0 (CED, mispeech); AudioSet class list CC BY 4.0",
        source=f"{HF}/{_CED[0]}",
        files=(
            _hf(_CED, "model.onnx", "model.onnx", 22785539,
                "18fa4fa30c1872c322c6b08f2824c9dd6f7fe149b8aa21320ddceb77007cff75"),
            _hf(_AUDIOSET_CLASSES, "class_labels_indices.csv", "class_labels_indices.csv", 14675,
                "cdd1049833c4b86127c2773ac0d14a2754b6a6d0d1798002ed5c66e699708429"),
        ),
    ),
    ModelSpec(
        id="ocr/pp-ocrv6-small",
        kind="ocr",
        label="PP-OCRv6 small (texte à l'écran, 50 langues)",
        label_en="PP-OCRv6 small (on-screen text, 50 languages)",
        licence="Apache-2.0 (PaddlePaddle PP-OCRv6)",
        source=f"{HF}/{_OCR_DET[0]}",
        files=(
            _hf(_OCR_DET, "inference.onnx", "det.onnx", 9880512,
                "d73e0058b7a8086bbd57f3d10b8bcd4ff95363f67e06e2762b5e814fe9c9410e"),
            _hf(_OCR_DET, "inference.yml", "det.yml", 885,
                "193f435274bf9f0b5f71a929bbfbcf148282df7e633b34e7c373e8f44741b516"),
            _hf(_OCR_REC, "inference.onnx", "rec.onnx", 21159378,
                "5435fd747c9e0efe15a96d0b378d5bd157e9492ed8fd80edf08f30d02fa24634"),
            _hf(_OCR_REC, "inference.yml", "rec.yml", 150579,
                "ab078671bb49f06228eadccd34f1bb501e157f7a047095ffb943ba81512c77d1"),
        ),
    ),
    ModelSpec(
        id="detector/d-fine-s-coco",
        kind="detector",
        label="D-FINE S (personnes et animaux courants, COCO)",
        licence="Apache-2.0 (D-FINE, conversion ONNX onnx-community)",
        label_en="D-FINE S (common people and animals, COCO)",
        licence_en="Apache-2.0 (D-FINE, ONNX conversion by onnx-community)",
        source=f"{HF}/{_DFINE_S[0]}",
        files=(
            _hf(_DFINE_S, "onnx/model.onnx", "model.onnx", 41535197,
                "cd8a49a945feda6d28c6304ae8ae85c2759ba1d78a5a83a22c5ce8db82ef7238"),
            _hf(_DFINE_S, "config.json", "config.json", 6656,
                "9338ef3863d6e95627d4ab06009fa85b1dd523b346b5c3595de2b08862136e99"),
        ),
    ),
    ModelSpec(
        id="runtime/cublas-12.9",
        kind="runtime",
        label="cuBLAS 12.9 (NVIDIA, pour transcrire sur le GPU)",
        licence="NVIDIA CUDA Toolkit EULA (bibliothèque redistribuable, non modifiée)",
        label_en="cuBLAS 12.9 (NVIDIA, to transcribe on the GPU)",
        licence_en="NVIDIA CUDA Toolkit EULA (redistributable library, unmodified)",
        source="https://pypi.org/project/nvidia-cublas-cu12/12.9.2.10/",
        files=(
            _cublas("cublasLt64_12.dll", 668673536,
                    "71705a7cf0923f4ab034d0d2620bbfc989ff669ac3d79f868b7a7c597ca559c5"),
            _cublas("cublas64_12.dll", 102518272,
                    "52ce5ba0ec5327d39be6021f0f9362d2ed4d64d7206f9b5afc06398d827b8e82"),
        ),
    ),
    ModelSpec(
        id="faces/yunet",
        kind="faces",
        label="YuNet (position des visages, sans identification)",
        label_en="YuNet (where the faces are, no identification)",
        licence="MIT (OpenCV Zoo, YuNet 2023mar)",
        source=f"{HF}/{_YUNET[0]}",
        files=(
            _hf(_YUNET, "face_detection_yunet_2023mar.onnx", "yunet.onnx", 232589,
                "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
            _hf(_YUNET, "LICENSE", "LICENSE", 1085,
                "c83b8120c50ccbd4c4f96edf53141bdd566ebb8f8e9227e415326aa1b1aba958"),
        ),
    ),
    ModelSpec(
        id="embeddings/embeddinggemma-300m-q4",
        kind="embeddings",
        label="EmbeddingGemma-300m q4 (recherche par le sens, FR/EN, CPU)",
        licence="Gemma Terms of Use (Google, https://ai.google.dev/gemma/terms), "
        "conversion ONNX onnx-community",
        label_en="EmbeddingGemma-300m q4 (search by meaning, FR/EN, CPU)",
        licence_en="Gemma Terms of Use (Google, https://ai.google.dev/gemma/terms), "
        "ONNX conversion by onnx-community",
        source=f"{HF}/{_GEMMA[0]}",
        files=(
            # The graph finds its weights by name: both keep the names of the repository.
            _hf(_GEMMA, "onnx/model_q4.onnx", "model_q4.onnx", 519322,
                "ad1dfee81a70f7944b9b9d1cc6e48075b832881cf33fab2f2b248be78f3f0043"),
            _hf(_GEMMA, "onnx/model_q4.onnx_data", "model_q4.onnx_data", 196725760,
                "599962c3143b040de2dd05e5975be3e9091dd067cacc6a8f7186e3203bab9e02"),
            _hf(_GEMMA, "tokenizer.json", "tokenizer.json", 20323312,
                "4dda02faaf32bc91031dc8c88457ac272b00c1016cc679757d1c441b248b9c47"),
        ),
    ),
)  # fmt: skip

DEFAULTS = {
    "whisper": "whisper/large-v3-turbo",
    "yamnet": "yamnet",
    "ced": "sounds/ced-small",
    "ocr": "ocr/pp-ocrv6-small",
    "detector": "detector/d-fine-s-coco",
    "faces": "faces/yunet",
    "cuda_runtime": "runtime/cublas-12.9",
    "embeddings": "embeddings/embeddinggemma-300m-q4",
}


def spec(model_id: str) -> ModelSpec:
    for item in CATALOG:
        if item.id == model_id:
            return item
    raise KeyError(model_id)
