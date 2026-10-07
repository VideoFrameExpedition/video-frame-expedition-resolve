# Third-party notices

Video Frame Expedition for DaVinci Resolve reuses ideas, algorithms, data and services from the
projects below. Code that is adapted from another project says so in a comment at the top of the
module.

## Source code

### Video Frame Expedition (legacy application by the same author)
Logic re-implemented (not copied): sun-phase/Kelvin classifier, Open-Meteo interpolation,
Nominatim lookup, prompt presets, ISO 6709 parsing, poster frame and orientation badges.

## Font (the logotype only, converted to outlines — no font file is bundled)

| Font | License | Use |
|---|---|---|
| [Epilogue](https://github.com/etunni/epilogue) (Etienne Aubert Bonn, Tunera Type Foundry), weights 400, 500 and 700 | SIL OFL 1.1 | The three lines of the logotype |

It is fetched from the [`@fontsource`](https://fontsource.org) package by `docs/brand/build.cjs`,
into a cache that is not versioned, and only its outlines end up in the SVG files (docs/brand/README.md).
The OFL allows this.

## Trademarks

"DaVinci Resolve" and "Blackmagic Design" are trademarks of Blackmagic Design Pty Ltd. This is an
independent application: it is not published, endorsed or supported by Blackmagic Design.

## Models (downloaded at runtime by `vfe models`, never bundled)

| Model | License | Use |
|---|---|---|
| YAMNet (Google, AudioSet) — tf2onnx conversion `zeropointnine/yamnet-onnx` | Apache-2.0 | Sounds and instruments |
| CED-small (Xiaomi / mispeech, `mispeech/ced-small`, ONNX) | Apache-2.0 | Second opinion on the sounds heard |
| cuBLAS 12.9 (NVIDIA, `cublasLt64_12.dll` and `cublas64_12.dll` taken unmodified from the `nvidia-cublas-cu12` 12.9.2.10 wheel on PyPI) | NVIDIA CUDA Toolkit EULA (redistributable component) | Whisper on the GPU when it is lent (Windows with an NVIDIA card only); downloaded only by `vfe models cuda-runtime`, which refuses on macOS |
| YuNet face detector 2023mar (OpenCV Zoo, `opencv/face_detection_yunet`) | MIT | Face positions and landmarks (never identities), for reframing |
| PP-OCRv6 small detection and recognition (PaddlePaddle, ONNX) | Apache-2.0 | On-screen text (own pre/post-processing, no RapidOCR) |
| Whisper large-v3-turbo, small, tiny (OpenAI; CTranslate2 conversions by dropbox-dash and SYSTRAN), run by faster-whisper and Silero VAD | MIT | Transcription |
| D-FINE-S COCO (ustc-community, ONNX by onnx-community `dfine_s_coco-ONNX`) | Apache-2.0 | Positions of people and common animals |
| EmbeddingGemma-300m q4 (Google; ONNX conversion by onnx-community, `onnx-community/embeddinggemma-300m-ONNX`, pinned revision) | Gemma Terms of Use (https://ai.google.dev/gemma/terms) | Search by meaning: passage and query embeddings on the CPU; downloaded only by `vfe models search` |

## Data

- `yamnet_class_map.csv` — YAMNet class map, Apache-2.0 (Google).
- `class_labels_indices.csv` — the 527 AudioSet class names (Google AudioSet, CC BY 4.0), as
  published in `k2-fsa/sherpa-onnx-ced-tiny-audio-tagging-2024-04-19`; read with CED-small.
- `ontology.json` — AudioSet ontology (Google, https://github.com/audioset/ontology),
  CC BY-SA 4.0: downloaded next to the YAMNet model, never modified; the sound families of
  `domain/audio_events.py` are derived from its hierarchy.
- **timezonefinder** (MIT) and its `timezonefinder-data` boundaries, derived from
  timezone-boundary-builder (© OpenStreetMap contributors, ODbL 1.0) — offline time zone of
  a GPS position, country and "at sea" for the offline gazetteer.
- **GeoNames** `cities1000`, `admin1CodesASCII`, `admin2Codes` (CC BY 4.0,
  https://www.geonames.org/) — offline gazetteer built by `vfe models geonames` into the data
  folder (never bundled); the snapshot date is recorded in its `SOURCE.txt`.
- **NOAA solar calculator** algorithm (US government work, public domain; after Meeus,
  *Astronomical Algorithms*) — reimplemented in `domain/sun.py`.
- Clear-sky colour temperatures derived from the SPECTRL2 model (Bird & Riordan 1986) — constants
  in `domain/sun.py`.

## Online services (optional, can be switched off in the settings)

- **OpenStreetMap / Nominatim** — geocoding data © OpenStreetMap contributors, ODbL 1.0.
  Usage policy: https://operations.osmfoundation.org/policies/nominatim/
- **Open-Meteo** — weather data CC BY 4.0, free tier for non-commercial use.
  https://open-meteo.com/en/terms — archive data: "Generated using Copernicus Climate Change
  Service information".
- **OpenStreetMap tiles** — © OpenStreetMap contributors. Tile usage policy:
  https://operations.osmfoundation.org/policies/tiles/

## Frontend libraries

- **Leaflet** 1.9 (BSD-2-Clause) — interactive map of the Context tab.

## External tools (installed separately, not redistributed)

- **FFmpeg** — LGPL/GPL depending on the build.
- **ExifTool** by Phil Harvey — Artistic License / GPL.
- **LM Studio** — proprietary, installed by the user.
