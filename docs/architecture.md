# Architecture

**English** · [Français](architecture.fr.md)

## Overview

```mermaid
flowchart LR
    Browser["Browser<br/>(React SPA)"] -- "REST /api/v1 + SSE" --> API
    Claude["Claude Code"] -- "MCP HTTP /mcp" --> API
    Claude -- "MCP" --> Resolve["ResolveMCP.exe<br/>(DaVinci Resolve Studio)"]
    API -- "child process<br/>fusionscript" --> ResolveApp["DaVinci Resolve Studio<br/>(reading, timeline creation,<br/>the assistant's Resolve tools)"]

    subgraph serve["vfe serve"]
        API["API process<br/>FastAPI + MCPServer"]
        Worker["Worker process<br/>(file lock, single)"]
        API -- "supervises" --> Worker
    end

    API <--> DB[("SQLite WAL<br/>FTS5 + vectors")]
    Worker <--> DB
    Worker --> Artifacts["Artefacts<br/>%LOCALAPPDATA%\\vfe-vision"]
    Worker --> FFmpeg["ffmpeg / ffprobe"]
    Worker --> ExifTool["ExifTool (-stay_open)"]
    Worker --> LMStudio["LM Studio, here or on another<br/>computer (VLM on GPU; loaded by<br/>the user, except model bench)"]
    Worker --> ASR["Subprocess<br/>faster-whisper"]
    Worker --> ONNX["ONNX Runtime CPU<br/>YAMNet · OCR · detector"]
    Worker -. "if enabled" .-> Web["Nominatim · Open-Meteo"]
```

## Backend layers

Dependencies only go downwards; `import-linter` checks this on every `just check`.

| Layer | Role |
|---|---|
| `cli` | `vfe` commands |
| `api`, `mcp` | inbound adapters: REST/SSE and MCP tools |
| `services` | shared use cases (library, analysis, search, reframing, exports…) |
| `jobs` | job queue, scheduler, resource pools, worker, supervisor |
| `pipeline` | stage contract, registry, chained cache keys, analysis stages |
| `adapters`, `db`, `storage` | ffmpeg, exiftool, LM Studio, ONNX, web services; persistence; artefacts |
| `ports` | interfaces with fakes for the tests (LM Studio, geocoding, weather, ASR, detector) |
| `domain` | pure logic: timecodes, GPS, capture time, sun, colour, boxes, reframing… |
| `core` | configuration, logging, errors, paths, processes |
