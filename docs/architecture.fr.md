# Architecture

[English](architecture.md) · **Français**

## Vue d'ensemble

```mermaid
flowchart LR
    Browser["Navigateur<br/>(React SPA)"] -- "REST /api/v1 + SSE" --> API
    Claude["Claude Code"] -- "MCP HTTP /mcp" --> API
    Claude -- "MCP" --> Resolve["ResolveMCP<br/>(DaVinci Resolve Studio)"]
    API -- "processus enfant<br/>fusionscript" --> ResolveApp["DaVinci Resolve Studio<br/>(lecture, création de timeline,<br/>outils Resolve de l'assistant)"]

    subgraph serve["vfe serve"]
        API["Processus API<br/>FastAPI + MCPServer"]
        Worker["Processus worker<br/>(verrou fichier, unique)"]
        API -- "supervise" --> Worker
    end

    API <--> DB[("SQLite WAL<br/>FTS5 + vecteurs")]
    Worker <--> DB
    Worker --> Artifacts["Artefacts<br/>%LOCALAPPDATA%\\vfe-vision ou<br/>~/Library/Application Support/vfe-vision"]
    Worker --> FFmpeg["ffmpeg / ffprobe"]
    Worker --> ExifTool["ExifTool (-stay_open)"]
    Worker --> LMStudio["LM Studio, ici ou sur un autre<br/>ordinateur (VLM sur GPU ; chargé par<br/>l'utilisateur, sauf banc d'essai)"]
    Worker --> ASR["Sous-processus<br/>faster-whisper"]
    Worker --> ONNX["ONNX Runtime CPU<br/>YAMNet · OCR · détecteur"]
    Worker -. "si activé" .-> Web["Nominatim · Open-Meteo"]
```

## Couches du backend

Les dépendances vont uniquement vers le bas ; `import-linter` le vérifie à chaque `just check`.

| Couche | Rôle |
|---|---|
| `cli` | commandes `vfe` |
| `api`, `mcp` | adaptateurs entrants : REST/SSE et outils MCP |
| `services` | cas d'usage partagés (bibliothèque, analyse, recherche, recadrage, exports…) |
| `jobs` | file de jobs, ordonnanceur, pools de ressources, worker, superviseur |
| `pipeline` | contrat d'étape, registre, clés de cache chaînées, étapes d'analyse |
| `adapters`, `db`, `storage` | ffmpeg, exiftool, LM Studio, ONNX, services web ; persistance ; artefacts |
| `ports` | interfaces à faux pour les tests (LM Studio, géocodage, météo, ASR, détecteur) |
| `domain` | logique pure : timecodes, GPS, heure de capture, soleil, couleur, boîtes, recadrage… |
| `core` | configuration, journalisation, erreurs, chemins, processus |

Windows et macOS partagent ce code : ce qui dépend du système est une branche
`sys.platform` dans l'adaptateur ou le module `core` concerné. Les processus enfants (worker,
transcription, scripts de Resolve) sont tenus par un Job Object sous Windows, par un groupe de
processus et un fil de vie ailleurs (`core/procs.py`).
