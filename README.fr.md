<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/brand/logo/lockup.svg">
  <img src="docs/brand/logo/lockup-light.svg" alt="Video Frame Expedition for DaVinci Resolve" height="96">
</picture>

[English](README.md) · **Français**

[![Checks](https://github.com/VideoFrameExpedition/video-frame-expedition-resolve/actions/workflows/checks.yml/badge.svg)](https://github.com/VideoFrameExpedition/video-frame-expedition-resolve/actions/workflows/checks.yml)

**Video Frame Expedition for DaVinci Resolve analyse vos rushs avec un modèle de vision local,
pour qu'un assistant IA monte en sachant ce que contient chaque plan.**

**Analyse :** métadonnées, lieu, heure, soleil et météo du tournage ; plans, images clés et
sujets ; sons, paroles et texte à l'écran. En français et en anglais.

**Recherche sémantique :** plans, images clés, paroles et chapitres deviennent des passages
horodatés, retrouvés par mots-clés et par le sens (recherche hybride : plein texte et vecteurs
calculés en local). Filtres par météo, lumière, lieu, dates, sujets, cadrage ou qualité ; le
modèle local répond aux questions sur toute la bibliothèque en citant ses sources.

**Base résiliente :** chaque vidéo est reconnue à son contenu, pas à son chemin : déplacée ou
renommée, elle garde ses analyses sans être réanalysée. Des fichiers d'analyse posés à côté des
vidéos les ramènent sur un autre ordinateur ou après la perte de la base.

**Pour les assistants IA** (Claude, Cursor, VS Code, Codex) : un serveur MCP de 25 outils.
L'assistant IA lit la timeline ouverte dans Resolve, sait ce que contient chaque plan, cherche des
plans dans toute la bibliothèque, obtient des points de coupe sûrs et des recadrages, et construit
le montage dans une nouvelle timeline. Les tâches simples (décrire des images, répondre sur la
bibliothèque, situer un sujet) vont au modèle local : l'assistant IA ne reçoit que les résultats et
économise ses tokens.

**Au-delà du MCP de Resolve :** quatre outils pilotent Resolve Studio 21.1 par des scripts figés
et testés plutôt que par du code réécrit à chaque demande : lecture de la timeline avec les plages
exactes des sources, recadrages (9:16…) centrés sur les sujets, nouvelle timeline construite puis
vérifiée valeur par valeur, marqueurs. Ils contournent les pièges connus de l'API de Resolve 21.1,
ne modifient jamais une timeline existante et pilotent aussi un Resolve distant, ce que le serveur
MCP de Blackmagic ne fait pas encore. L'assistant IA peut toujours intervenir sur la timeline
ouverte via le MCP de Resolve.

**Avec DaVinci Resolve, dans les deux sens :** les vidéos choisies deviennent une timeline avec
sous-titres et marqueurs ; une timeline de Resolve entre dans la bibliothèque et ses vidéos sont
analysées.

**Stand-alone :** une interface web pour parcourir les analyses, chercher et interroger toute la
bibliothèque.

**Windows et macOS.** Ce dépôt contient l'application pour Windows 11 et pour les Mac à puce
Apple Silicon : un seul code, une installation par système. Elle a été développée sous Windows,
avec une carte graphique NVIDIA ; sur un Mac, les analyses, vidéos HDR comprises, et le lien
avec DaVinci Resolve ont été essayés sur un Mac M1.

> Présentation en vidéo, huit minutes : [youtu.be/1EI36bRbdWo](https://youtu.be/1EI36bRbdWo) ;
> en anglais : [youtu.be/G0WT96QsGsU](https://youtu.be/G0WT96QsGsU).
>
> C'est la nouvelle version de *Video Frame Expedition* ; elle remplace l'ancienne.

Vos images et vos sons ne quittent jamais votre machine, sauf, si vous le choisissez, vers le
LM Studio d'un autre de vos ordinateurs ; le modèle de vision tourne sur votre carte graphique.
Les analyses ne font que deux appels réseau (une position arrondie et une date, pour trouver le
lieu et la météo), et un seul interrupteur de la page Système les coupe ; le même interrupteur
masque la carte OpenStreetMap de l'onglet Contexte, qui charge ses tuiles depuis internet. La
page d'aide charge ses polices depuis Google Fonts, et ses vidéos de présentation depuis YouTube
(youtube-nocookie.com, seulement quand vous arrivez à leur hauteur).

## Principes

- **Local d'abord** : les images et les sons ne quittent jamais votre machine. Les seuls envois
  (coordonnées GPS et date, pour le lieu et la météo) se coupent dans la page Système, avec la
  carte de l'onglet Contexte.
- **Escalade entre modèles** (*escalation*) : les tâches simples vont au modèle local, sur votre
  machine — analyser les vidéos, répondre à une question sur la bibliothèque (`ask_library`),
  situer un sujet dans une image (`plan_reframe`) — et l'assistant n'en reçoit que le résultat.
  L'étage complexe — comprendre une demande, choisir les plans, monter — revient au modèle
  frontière (Claude…), plus coûteux. Il ne regarde plus des centaines d'images ni n'écrit de
  scripts pour Resolve : il consomme beaucoup moins de tokens.
- **Vos rushs restent intacts** : la seule chose écrite dans vos dossiers de vidéos est un
  petit fichier d'analyse par vidéo et par langue (`<nom>_FR.txt`, `<nom>_EN.txt` ; JSON, sans
  images), désactivable dans la page Système. Il permet de retrouver les analyses sans les
  refaire (base perdue, autre ordinateur) et contient la position GPS et ce qui est dit :
  partager le dossier les partage aussi. Quand vous créez une timeline dans DaVinci Resolve avec
  des sous-titres, chaque vidéo reçoit aussi les siens : `<nom>_FR.srt` (ce qui est dit, dans la
  langue parlée) et `<nom>_SHOTS_FR.srt` (les plans, dans la langue de l'interface). Un fichier
  que l'application n'a pas écrit, ou que vous avez modifié, n'est jamais remplacé.
- **Le modèle de vision reste sur le GPU** : l'application utilise le modèle que vous avez chargé
  dans LM Studio, sans jamais le recharger ni en charger d'autres pendant les analyses. Elle ne
  décode les vidéos sur le GPU que si ce modèle laisse assez de mémoire libre (par exemple avec
  `qwen/qwen3-vl-4b`).
- **Choisir son modèle de vision** : la page « Banc d'essai » compare les modèles de LM Studio
  que vous cochez, sur des images de votre bibliothèque. Chacun est chargé seul, interrogé comme
  le font les analyses, puis déchargé ; un tableau donne la mémoire occupée sur la carte, le
  temps par image, les réponses valides, les réponses dans la langue demandée, le texte lu et
  les positions, et vous notez les descriptions à l'aveugle. Un classement, des profils et des
  graphiques résument ces mesures, et un historique garde chaque test : le classement général y
  compare le dernier résultat de chaque modèle, tous tests confondus. C'est le seul endroit où
  l'application charge un modèle, à votre demande ; elle recharge ensuite celui qui était là.
- **LM Studio ici ou ailleurs** : par défaut, l'application parle au LM Studio de l'ordinateur.
  La carte « LM Studio » de la page Système en désigne un autre — un PC de votre réseau local ou
  de Tailscale, dont la carte graphique est plus puissante —, le teste avant de l'adopter et
  garde les connexions passées à portée de clic. Les images de vos vidéos partent alors vers cet
  ordinateur, et seulement vers lui.
- **Deux langues** : chaque analyse existe en français et en anglais. Les modèles écrivent dans
  une langue (page Système), puis l'étape « Traduction » traduit leurs textes dans l'autre, sans
  rien refaire ; l'interface les montre, et les exporte (fichiers, timelines, sous-titres), dans
  sa propre langue. Les noms des fichiers finissent par leur langue : `_FR`, `_EN`.
- **Une analyse faite est gardée** : relancer l'analyse ne fait que ce qui manque. « Mettre à
  jour » et « Tout refaire » sont des choix explicites.
- **Sons, parole et texte sur le CPU** : YAMNet (sons et instruments) avec le second avis de
  CED-small (« sons entendus » : oiseaux, grenouilles, insectes, pluie, pas… en français, avec
  leurs moments), Whisper large-v3-turbo (transcription, dans un processus séparé) et PP-OCRv6
  (texte à l'écran) tournent sur le processeur. Leurs modèles sont téléchargés une fois par le
  script d'installation. En option (page Système), sous Windows avec une carte NVIDIA,
  Whisper peut emprunter le GPU quand le modèle de vision laisse assez de mémoire
  (`vfe models cuda-runtime`).
- **Où sont les sujets** : un cadre autour de chaque être vivant (personnes, animaux, insectes)
  sur les images clés, positions réutilisables pour recadrer (MCP `get_object_locations`). Le
  modèle de vision déjà chargé repère tout, puis D-FINE et YuNet (sur le CPU) resserrent les
  cadres et complètent les foules. Positions seulement : personne n'est identifié.
- **Ce qui se passe dans chaque plan** : le modèle de vision raconte chaque plan à partir de
  plusieurs de ses images, dans l'ordre (un insecte qui s'envole, une main qui ajoute un
  ingrédient), avec l'heure de chaque image. Onglet Plans, frise et MCP `get_shots`.
- **Tout retrouver** : la page Recherche cherche dans toute la bibliothèque ce qui se voit, se
  dit, s'entend ou se lit, par les mots et par le sens (EmbeddingGemma, sur le CPU), avec des
  filtres (météo, lumière, lieu, dates, sujets, cadrage…) ; un clic ouvre la vidéo au bon
  moment. MCP `search_memory` et `find_clips` (plans prêts pour Resolve).
- **Poser des questions** : la page Questions répond à une question sur toute la bibliothèque
  avec le modèle chargé, en citant ses sources (un clic ouvre la vidéo au bon moment), avec une
  vérification sur les images en option. MCP `ask_library`.
- **Exporter** : onglet Exports de chaque vidéo (sous-titres SRT/VTT, plans en CSV pour Excel,
  chapitres YouTube, EDL de marqueurs, analyse JSON, fiche MANIFEST, script Resolve) et tableau
  CSV des vidéos sélectionnées. Rien n'est écrit à côté des vidéos.
- **Créer une timeline** : les vidéos cochées, bout à bout, dans l'ordre de tournage (ou autre),
  en fichier à importer dans DaVinci Resolve (Fichier › Importer › Timeline) ou, quand
  Resolve est ouvert, directement dans le projet en cours ; au choix, la transcription et la
  description des plans en sous-titres (une piste chacune), les suggestions en marqueurs de durée
  et les chapitres en marqueurs. Le téléchargement est un ZIP : OTIO pour Resolve, FCPXML pour
  Final Cut Pro, fichiers SRT et LISEZ-MOI.txt. En création directe, les sous-titres sont posés
  sur la timeline (une piste « Transcription », une piste « Plans ») et écrits aussi à côté de
  chaque vidéo.

## Prérequis

- **L'application** : Windows 11, ou un Mac à puce Apple Silicon (M1 ou plus récente) sous
  macOS 15 ou plus récent, ce que demande DaVinci Resolve 21. Le même code sert aux deux.
  [uv](https://docs.astral.sh/uv/), Node.js 24 LTS (l'interface web est construite au premier
  lancement), FFmpeg et ExifTool : `scripts/bootstrap.ps1` (Windows) ou
  `scripts/bootstrap.sh` (Mac) les installe. Sur Mac, FFmpeg vient dans sa version complète,
  `ffmpeg-full`, dont le filtre zscale convertit les vidéos HDR en images pour le modèle de
  vision.
- **LM Studio**, avec le serveur local activé et un modèle de vision chargé (ex.
  `qwen/qwen3-vl-8b`) : sur le même ordinateur ou sur un autre, sous Windows, macOS ou Linux.
- **DaVinci Resolve Studio 21.1 ou plus récent**, pour le lien avec Resolve : sur le même
  ordinateur ou sur un autre, sous Windows, macOS ou Linux.

**Sous Windows**, l'application a été développée et testée avec une carte graphique NVIDIA,
qu'elle utilise quand le modèle de vision laisse assez de mémoire (décodage des vidéos et, en
option, parole). Sans cette carte, le même travail se fait sur le processeur. **Sur un Mac**, le
modèle de vision tourne dans LM Studio sur les cœurs graphiques de la puce et partage la mémoire
unifiée avec Resolve et l'application ; le décodage des vidéos, la parole, le texte à l'écran et
les autres modèles tournent sur le processeur. 32 Go de mémoire sont confortables pour un modèle
de vision de 8 milliards de paramètres à côté de Resolve ; avec 16 Go, préférez un modèle de
4 milliards et un contexte court.

## Installation

Récupérez d'abord l'application :
`git clone https://github.com/VideoFrameExpedition/video-frame-expedition-resolve.git`,
ou le bouton « Code › Download ZIP » de GitHub, puis décompressez-la.

### Sous Windows

1. Dans PowerShell, depuis le dossier de l'application :

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
   ```

   Le script installe par `winget` ce qui manque (uv, Node.js, FFmpeg, ExifTool), puis les
   paquets Python de l'application et ses modèles (environ 2 Go, une seule fois ;
   `-SansModeles` pour s'en passer). Il demande s'il faut installer LM Studio sur ce PC : le
   modèle de vision peut aussi tourner dans le LM Studio d'un autre ordinateur. `-AvecLMStudio`
   ou `-SansLMStudio` donne la réponse d'avance. Acceptez les demandes d'autorisation de Windows
   (UAC). Ses messages sont en français sur un Windows en français, en anglais sinon ;
   `VFE_LANG=fr` ou `VFE_LANG=en` dans le fichier `.env` impose l'une des deux langues, pour
   `run.bat` et les commandes `vfe` aussi.
2. Dans LM Studio, téléchargez un modèle de vision (par exemple `qwen/qwen3-vl-8b`), chargez-le
   et activez le serveur local (voir plus bas quand il tourne sur un autre ordinateur).
3. Double-cliquez sur `run.bat`. La première fois, il construit l'interface web (une à deux
   minutes), puis ouvre le navigateur.

### Sur un Mac

1. Dans le Terminal, depuis le dossier de l'application :

   ```sh
   sh scripts/bootstrap.sh
   ```

   Le script installe par [Homebrew](https://brew.sh) ce qui manque (uv, Node.js, FFmpeg en
   version complète, ExifTool), puis les paquets Python de l'application et ses modèles (environ
   2 Go, une seule fois ; `--sans-modeles` pour s'en passer). Il demande s'il faut installer
   LM Studio sur ce Mac ; `--avec-lm-studio` ou `--sans-lm-studio` donne la réponse d'avance.
   Homebrew lui-même est installé d'abord si le Mac ne l'a pas (il demande votre mot de passe).
   Ses messages suivent la langue du Mac (français ou anglais), comme ceux de `run.command` ;
   `VFE_LANG` dans le fichier `.env` peut l'imposer.
2. Dans LM Studio, téléchargez un modèle de vision (par exemple `qwen/qwen3-vl-4b`, ou
   `qwen/qwen3-vl-8b` avec 32 Go de mémoire), chargez-le et activez le serveur local.
3. Double-cliquez sur `run.command`. La première fois, il construit l'interface web (une à deux
   minutes), puis ouvre le navigateur. macOS peut demander si le Terminal peut accéder à vos
   Vidéos, à vos Documents ou à un disque externe : acceptez, l'application y lit vos vidéos.

   Si macOS refuse d'ouvrir `run.command` la première fois (un fichier téléchargé en ZIP porte
   une marque de quarantaine ; un dossier obtenu par `git clone` n'en a pas), autorisez-le dans
   Réglages Système › Confidentialité et sécurité, ou retirez la marque dans le Terminal, depuis
   le dossier de l'application : `xattr -dr com.apple.quarantine .`

### LM Studio sur un autre ordinateur

Quand LM Studio tourne sur un autre ordinateur, téléchargez et chargez le modèle là-bas, et
laissez son serveur accepter le réseau local (Developer › Server Settings › « Serve on Local
Network ») ; une fois l'application ouverte, donnez son adresse page Système, carte
« LM Studio ».

**Ligne de commande.** Dans cette page, `vfe <commande>` désigne la commande suivante, tapée
dans PowerShell (sur un Mac, dans le Terminal) depuis le dossier de l'application :

```powershell
uv run --frozen --no-dev --project backend python -m vfe_vision <commande>
```

Par exemple, `vfe doctor` vérifie FFmpeg, ExifTool, LM Studio et le GPU (sur un Mac, la puce
et sa mémoire).

## Démarrage rapide

**Au quotidien : double-cliquez sur `run.bat` (Windows) ou `run.command` (Mac).** Il démarre
l'application (interface, MCP et analyses) et ouvre le navigateur sur http://127.0.0.1:8765.
S'il est déjà lancé, il ouvre simplement l'interface. `run.bat build` (ou `run.command build`)
reconstruit d'abord l'interface web après une mise à jour. Pour arrêter l'application, fermez
sa fenêtre (sur Mac, celle du Terminal, ou Ctrl+C).

**La page « Aide »** de la barre latérale est le guide complet : douze parties, les sept onglets
d'une vidéo un par un, une cinquantaine de captures de l'interface en français, avec le texte
en français et en anglais. C'est le fichier
[`frontend/public/help/index.html`](frontend/public/help/index.html), servi à
http://127.0.0.1:8765/help/index.html ; le dossier peut aussi être hébergé tel quel ailleurs.

**La vidéo de présentation** (huit minutes, dix chapitres) est en ligne sur
[YouTube](https://youtu.be/1EI36bRbdWo) et dans la page d'aide ; sa version anglaise (neuf
minutes) aussi, sur [YouTube](https://youtu.be/G0WT96QsGsU) et dans la page d'aide en anglais.

Assistants (Claude Code, Claude Desktop, Cursor, VS Code, Codex) et accès depuis vos autres
appareils par Tailscale : page « Connexions » de l'interface et
[guide](docs/guide/connect-mcp.fr.md).

Réglages lus au démarrage (adresse et port, chemins des outils, adresse de LM Studio) : copiez
[`docs/env.example`](docs/env.example) en fichier `.env` à côté de `run.bat` (ou de
`run.command`). Tout le reste se règle dans l'interface.

## Développement

Les tâches de développement passent par [just](https://just.systems) (`winget install
Casey.Just` sous Windows, `brew install just` sur Mac) ; chaque recette du `justfile` se lance
aussi à la main, par exemple si Smart App Control bloque `just.exe` sous Windows. Le typage se
vérifie pour les trois systèmes : `mypy --platform win32`, `darwin` et `linux`.

```powershell
just setup      # dépendances backend + frontend, hook pre-commit
just build      # construit l'interface web
just serve      # lance l'application sur http://127.0.0.1:8765
```

| Commande | Rôle |
|---|---|
| `just dev-backend` / `just dev-frontend` | serveurs de développement (API :8765, Vite :5173) |
| `just check` | lint, typage strict, contrats d'architecture et tests (backend + frontend) |
| `just test-live` | tests qui utilisent LM Studio, les modèles, le GPU ou internet |
| `just gen-client` | régénère le schéma OpenAPI et le client TypeScript |

## Monter avec Claude Code et DaVinci Resolve

Le serveur `vfe-vision` compte 25 outils. Quatre d'entre eux pilotent DaVinci Resolve Studio 21.1
à la place de l'assistant, quand la case **« Outils Resolve pour l'assistant »** de la page
Connexions est cochée (désactivée par défaut). La démarche :

1. `read_timeline` lit la timeline ouverte et relie chaque clip à sa vidéo analysée, avec des
   plages justes en secondes ; `match_clips` dit ce que contient chaque plage ;
2. Claude choisit les plans (`find_clips`, `get_synthesis`, `get_frames`) et demande à
   `get_cut_points` des entrées et sorties sûres (jamais dans un mot, J-cut et L-cut) ;
3. `plan_reframe` prépare le recadrage pour une autre forme (9:16…) : le travail d'image se
   fait en local, avec le modèle de vision chargé dans LM Studio (réponses gardées en cache), et
   Claude ne regarde que les planches de contrôle des plans signalés ;
4. `build_timeline` construit une timeline **neuve** « … - vfe vN » avec ces plans et ces
   recadrages, relit chaque durée et chaque valeur, et `apply_markers` pose chapitres, moments
   forts et métadonnées. Aucune timeline existante n'est modifiée et **le projet n'est pas
   enregistré** : Ctrl+S (Cmd+S sur Mac) dans Resolve si vous gardez le montage.

Sans la case, Claude écrit les mêmes étapes en scripts pour le serveur MCP de DaVinci Resolve
Studio (`match_clips`, `get_reframe`, `get_resolve_payload` fournissent les données ; l'invite
`plan_edit` décrit cette démarche et lui fait travailler dans une copie de la timeline), avec des
coupes franches et des fondus enchaînés seulement.

Un plugin Claude Code connecte Claude Code à ce serveur et ajoute un skill qui contient la méthode
de montage :
[video-frame-expedition-claude-plugin](https://github.com/VideoFrameExpedition/video-frame-expedition-claude-plugin).
Installation : `/plugin marketplace add VideoFrameExpedition/video-frame-expedition-claude-plugin`,
puis `/plugin install video-frame-expedition@video-frame-expedition`.

Claude n'ajoute un nouveau dossier à la bibliothèque (`analyze_folder`) que si la page Système
l'autorise. Tous les outils : [docs/mcp-tools.fr.md](docs/mcp-tools.fr.md).

## Documentation

- [Architecture](docs/architecture.fr.md)
- [Outils, ressource et invites du serveur MCP](docs/mcp-tools.fr.md)
- [Guides d'utilisation](docs/guide/)
- [Sécurité](SECURITY.fr.md) · [Licences tierces](THIRD_PARTY_NOTICES.md) (en anglais)
- [Le logo](docs/brand/README.fr.md)

L'application a été développée en français. L'interface, sa page d'aide, cette page et les
documents ci-dessus existent dans les deux langues, comme les messages des lanceurs, des
scripts d'installation et de la ligne de commande, qui suivent la langue du système.

## Licence

Gratuit, au code source ouvert, sous la [licence Apache 2.0](LICENSE) (texte officiel en
anglais). Vous pouvez l'utiliser, le modifier, l'intégrer et le partager, y compris pour votre
travail rémunéré, en gardant la mention de copyright et le fichier [NOTICE](NOTICE). Fourni tel
quel, sans garantie ni support.

## Contributions

Ce dépôt est publié pour que l'application puisse être installée et son code lu. Il ne prend pas
de contributions de code : les demandes de fusion (pull requests) ne sont pas intégrées. Pour
signaler un bug, [ouvrez une issue](https://github.com/VideoFrameExpedition/video-frame-expedition-resolve/issues/new/choose) : le formulaire demande le système (Windows ou macOS, et sa version), la
carte graphique ou le Mac, le modèle chargé dans LM Studio et le message d'erreur. Pour signaler une faille
de sécurité, voir [SECURITY.fr.md](SECURITY.fr.md).

Projet indépendant, ni affilié à Blackmagic Design ou à Anthropic, ni approuvé par eux ; DaVinci
Resolve, Claude et les autres produits cités sont des marques de leurs propriétaires respectifs.
