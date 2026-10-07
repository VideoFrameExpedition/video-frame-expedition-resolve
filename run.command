#!/bin/bash
# Video Frame Expedition for DaVinci Resolve - macOS launcher (double-click in the Finder).
#   run.command            starts the application and opens the browser
#   run.command build      first rebuilds the web interface (after an update)
#   run.command tailscale  also listens on this computer's Tailscale address (your other
#                          devices, token required); or VFE_TAILSCALE=true in the .env file
# The messages on screen are in French.
set -u
cd "$(dirname "$0")" || exit 1
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

fail() {
  echo
  echo "[ERREUR] $1"
  echo "Appuyez sur Entrée pour fermer cette fenêtre."
  read -r _
  exit 1
}

# --- Already running? Just open the interface. ---------------------------------------------
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo
  echo " L'application tourne déjà : ouverture de $URL"
  echo " Pour la voir dans une fenêtre (journal, Ctrl+C pour arrêter), fermez d'abord"
  echo " l'instance en cours, puis relancez run.command."
  echo
  [ -z "${VFE_NO_BROWSER:-}" ] && open "$URL"
  exit 0
fi

# --- Required tools -------------------------------------------------------------------------
command -v uv >/dev/null 2>&1 \
  || fail "« uv » est introuvable. Installez-le avec : brew install uv (ou lancez : sh scripts/bootstrap.sh)"
command -v ffmpeg >/dev/null 2>&1 \
  || echo "[ATTENTION] ffmpeg est introuvable : l'analyse des vidéos échouera (brew install ffmpeg)."
command -v exiftool >/dev/null 2>&1 \
  || echo "[ATTENTION] ExifTool est introuvable : pas de métadonnées ni de date de tournage (brew install exiftool)."

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
    || fail "Node.js est introuvable : impossible de construire l'interface web. Installez-le avec : brew install node"
  if [ ! -f frontend/node_modules/vite/bin/vite.js ]; then
    # pnpm runs through Node (npx), in the version pinned by frontend/package.json: its
    # lockfile, written as two documents, can only be read by pnpm 12.
    echo "Installation des dépendances de l'interface web..."
    npx --yes pnpm@12.6.0 --dir frontend install --frozen-lockfile \
      || fail "Les dépendances de l'interface web n'ont pas pu être installées."
  fi
  echo "Construction de l'interface web..."
  (cd frontend && node node_modules/typescript/bin/tsc -b && node node_modules/vite/bin/vite.js build) \
    || fail "La construction de l'interface web a échoué (voir les messages ci-dessus)."
  uv run --frozen --no-dev --project backend python scripts/copy_frontend_build.py \
    || fail "La copie de l'interface construite a échoué."
fi

echo
echo " Video Frame Expedition for DaVinci Resolve  -  $URL"
echo " Pensez à lancer LM Studio avec le modèle de vision chargé."
echo " Assistants (Claude, Cursor...) et accès depuis vos autres appareils : page « Connexions »."
echo " Pour arrêter l'application : fermez cette fenêtre ou appuyez sur Ctrl+C."
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
fail "L'application s'est arrêtée sur une erreur (code $status, voir les messages ci-dessus)."
