#!/bin/bash
# Video Frame Expedition for DaVinci Resolve - macOS launcher (double-click in the Finder).
#   run.command            starts the application and opens the browser
#   run.command build      first rebuilds the web interface (after an update)
#   run.command tailscale  also listens on this computer's Tailscale address (your other
#                          devices, token required); or VFE_TAILSCALE=true in the .env file
# The messages on screen are in English, or in French on a Mac set to French
# (scripts/language.sh; VFE_LANG=fr or en in the .env file decides).
set -u
cd "$(dirname "$0")" || exit 1
. scripts/language.sh
PORT=8765
URL="http://127.0.0.1:$PORT"
export PYTHONUTF8=1

# --- Programs installed by Homebrew or uv: a Terminal opened by the Finder may not have them ---
for dir in /opt/homebrew/bin /usr/local/bin "$HOME/.local/bin"; do
  case ":$PATH:" in
    *":$dir:"*) ;;
    *) [ -d "$dir" ] && PATH="$PATH:$dir" ;;
  esac
done
export PATH

# fail <French> <English>
fail() {
  echo
  say "[ERREUR] $1" "[ERROR] $2"
  say "Appuyez sur Entrée pour fermer cette fenêtre." "Press Return to close this window."
  read -r _
  exit 1
}

# --- Already running? Just open the interface. ---------------------------------------------
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo
  say " L'application tourne déjà : ouverture de $URL" \
    " The application is already running: opening $URL"
  say " Pour la voir dans une fenêtre (journal, Ctrl+C pour arrêter), fermez d'abord" \
    " To see it in a window (log, Ctrl+C to stop), first close the instance"
  say " l'instance en cours, puis relancez run.command." \
    " that is running, then start run.command again."
  echo
  [ -z "${VFE_NO_BROWSER:-}" ] && open "$URL"
  exit 0
fi

# --- Required tools -------------------------------------------------------------------------
command -v uv >/dev/null 2>&1 \
  || fail "« uv » est introuvable. Installez-le avec : brew install uv (ou lancez : sh scripts/bootstrap.sh)" \
    "\"uv\" cannot be found. Install it with: brew install uv (or run: sh scripts/bootstrap.sh)"
command -v ffmpeg >/dev/null 2>&1 \
  || say "[ATTENTION] ffmpeg est introuvable : l'analyse des vidéos échouera (brew install ffmpeg)." \
    "[WARNING] ffmpeg cannot be found: the analysis of the videos will fail (brew install ffmpeg)."
command -v exiftool >/dev/null 2>&1 \
  || say "[ATTENTION] ExifTool est introuvable : pas de métadonnées ni de date de tournage (brew install exiftool)." \
    "[WARNING] ExifTool cannot be found: no metadata nor shooting date (brew install exiftool)."

# --- Options: "build" (interface rebuilt), "tailscale" (access from your devices) -----------
BUILD=""
for arg in "$@"; do
  case "$arg" in
    build) BUILD=1 ;;
    tailscale) export VFE_TAILSCALE=true ;;
  esac
done

# --- Web interface: built when missing, or on request (run.command build) -------------------
[ -f backend/src/vfe_vision/web/dist/index.html ] || BUILD=1
if [ -n "$BUILD" ]; then
  command -v node >/dev/null 2>&1 \
    || fail "Node.js est introuvable : impossible de construire l'interface web. Installez-le avec : brew install node" \
      "Node.js cannot be found: the web interface cannot be built. Install it with: brew install node"
  if [ ! -f frontend/node_modules/vite/bin/vite.js ]; then
    # pnpm runs through Node (npx), in the version pinned by frontend/package.json: its
    # lockfile, written as two documents, can only be read by pnpm 12.
    say "Installation des dépendances de l'interface web..." \
      "Installing the web interface's dependencies..."
    npx --yes pnpm@12.6.0 --dir frontend install --frozen-lockfile \
      || fail "Les dépendances de l'interface web n'ont pas pu être installées." \
        "The web interface's dependencies could not be installed."
  fi
  say "Construction de l'interface web..." "Building the web interface..."
  (cd frontend && node node_modules/typescript/bin/tsc -b && node node_modules/vite/bin/vite.js build) \
    || fail "La construction de l'interface web a échoué (voir les messages ci-dessus)." \
      "Building the web interface failed (see the messages above)."
  uv run --frozen --no-dev --project backend python scripts/copy_frontend_build.py \
    || fail "La copie de l'interface construite a échoué." \
      "Copying the built interface failed."
fi

echo
echo " Video Frame Expedition for DaVinci Resolve  -  $URL"
say " Pensez à lancer LM Studio avec le modèle de vision chargé." \
  " Remember to start LM Studio with the vision model loaded."
say " Assistants (Claude, Cursor...) et accès depuis vos autres appareils : page « Connexions »." \
  " Assistants (Claude, Cursor...) and access from your other devices: \"Connections\" page."
say " Pour arrêter l'application : fermez cette fenêtre ou appuyez sur Ctrl+C." \
  " To stop the application: close this window or press Ctrl+C."
echo

# --- Opens the browser as soon as the server answers (in the background) --------------------
if [ -z "${VFE_NO_BROWSER:-}" ]; then
  (
    for _ in $(seq 1 240); do
      if curl -fs -m 2 "$URL/api/v1/system/health" >/dev/null 2>&1; then
        open "$URL"
        exit 0
      fi
      sleep 0.5
    done
  ) &
fi

# --frozen: the versions of uv.lock, which is never rewritten; --no-dev: no development tools.
uv run --frozen --no-dev --project backend python -m vfe_vision serve --port "$PORT"
status=$?
# Ctrl+C or a request to stop (SIGINT gives 130, SIGTERM 143): the application stopped as asked.
case "$status" in
  0 | 130 | 143) exit 0 ;;
esac
fail "L'application s'est arrêtée sur une erreur (code $status, voir les messages ci-dessus)." \
  "The application stopped on an error (code $status, see the messages above)."
