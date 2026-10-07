#!/bin/sh
# Installs Video Frame Expedition for DaVinci Resolve on a Mac (Apple Silicon, macOS 15 or
# later): the missing programs (through Homebrew), the application's Python packages, then its
# models.
#
#   sh scripts/bootstrap.sh
#   sh scripts/bootstrap.sh --sans-modeles     # without the application's models
#   sh scripts/bootstrap.sh --avec-lm-studio   # install LM Studio without asking
#   sh scripts/bootstrap.sh --sans-lm-studio   # leave LM Studio out without asking
#
# LM Studio, which runs the vision model, is installed only when the person running the script
# wants it: the question is asked unless it was answered in advance, and LM Studio is left out
# when nobody is at the keyboard. The vision model can also come from the LM Studio of another
# computer (System page of the application).
#
# Safe to run again: what is already there is left as it is. Neither pnpm nor just is needed:
# run.command builds the interface with Node only. The messages on screen are in French.
set -eu
cd "$(dirname "$0")/.."

say() { printf '%s\n' "$*"; }

SANS_MODELES=""
LM_STUDIO="" # oui or non: the answer given in advance
for arg in "$@"; do
  case "$arg" in
    --sans-modeles) SANS_MODELES=1 ;;
    --avec-lm-studio | --sans-lm-studio)
      choice=oui
      [ "$arg" = "--sans-lm-studio" ] && choice=non
      if [ -n "$LM_STUDIO" ] && [ "$LM_STUDIO" != "$choice" ]; then
        say "Choisissez --avec-lm-studio ou --sans-lm-studio, pas les deux."
        exit 2
      fi
      LM_STUDIO=$choice
      ;;
    *)
      say "Option inconnue : $arg (options : --sans-modeles, --avec-lm-studio, --sans-lm-studio)"
      exit 2
      ;;
  esac
done

if [ "$(uname -s)" != "Darwin" ]; then
  say "Ce script installe l'application sur un Mac."
  exit 1
fi
if [ "$(uname -m)" != "arm64" ]; then
  say "[ATTENTION] Ce Mac n'a pas de puce Apple Silicon : LM Studio et DaVinci Resolve 21 ne"
  say "            tournent pas dessus, et les paquets Python de l'application non plus."
fi

# Homebrew's folders, for a Terminal that does not have them in its PATH yet.
for dir in /opt/homebrew/bin /usr/local/bin "$HOME/.local/bin"; do
  case ":$PATH:" in
    *":$dir:"*) ;;
    *) [ -d "$dir" ] && PATH="$PATH:$dir" ;;
  esac
done
export PATH

if ! command -v brew >/dev/null 2>&1; then
  say "[..] Installation de Homebrew (https://brew.sh) : votre mot de passe sera demandé"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  if [ -x /opt/homebrew/bin/brew ]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [ -x /usr/local/bin/brew ]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi
fi

# install_if_missing <name> <probe command> <formula> [--cask]
install_if_missing() {
  if sh -c "$2" >/dev/null 2>&1; then
    say "[ok] $1"
    return
  fi
  say "[..] Installation de $1 (brew install ${4:-} $3)"
  # shellcheck disable=SC2086 # ${4:-} is empty or the single word --cask
  brew install ${4:-} "$3"
}

install_if_missing "uv" "command -v uv" "uv"
install_if_missing "Node.js" "command -v node" "node"
# FFmpeg's full build: its zscale filter turns HDR footage into the frames the vision model is
# shown. Keg-only, it stays out of the PATH; the application finds it in its own folder.
install_if_missing "FFmpeg (version complète)" \
  "test -x /opt/homebrew/opt/ffmpeg-full/bin/ffmpeg || test -x /usr/local/opt/ffmpeg-full/bin/ffmpeg" \
  "ffmpeg-full"
install_if_missing "ExifTool" "command -v exiftool" "exiftool"
LM_STUDIO_APP="/Applications/LM Studio.app"
if [ -d "$LM_STUDIO_APP" ]; then
  say "[ok] LM Studio"
else
  if [ -z "$LM_STUDIO" ]; then
    LM_STUDIO=non
    if [ -t 0 ]; then
      say ""
      say "LM Studio fait tourner le modèle de vision. Il peut être installé sur ce Mac, ou rester"
      say "sur un autre ordinateur du réseau (son adresse se donne dans l'application, page Système)."
      printf '%s' "Installer LM Studio sur ce Mac ? [o/N] "
      read -r reponse || reponse=""
      case "$reponse" in
        [oOyY]*) LM_STUDIO=oui ;;
      esac
    fi
  fi
  if [ "$LM_STUDIO" = oui ]; then
    say "[..] Installation de LM Studio (brew install --cask lm-studio)"
    brew install --cask lm-studio
  else
    say "[--] LM Studio n'est pas installé sur ce Mac"
  fi
fi

# Exactly the versions of uv.lock (--locked), without the development tools (--no-dev);
# --inexact leaves alone what a developer installed on top.
say "[..] Paquets Python de l'application"
uv sync --locked --no-dev --inexact --project backend

if [ -z "$SANS_MODELES" ]; then
  # Offline places, sounds, speech and on-screen text, subjects, search by meaning: ~2 GB.
  for pack in geonames audio-text subjects search; do
    say "[..] Modèles : $pack"
    uv run --frozen --no-dev --project backend python -m vfe_vision models "$pack"
  done
fi

chmod +x run.command 2>/dev/null || true
say ""

if [ ! -d "$LM_STUDIO_APP" ]; then
  say "Terminé. Sur l'ordinateur qui a LM Studio, chargez un modèle de vision et laissez son"
  say "serveur accepter le réseau local (Developer › Server Settings › « Serve on Local Network »)."
  say "Double-cliquez ensuite sur run.command ; dans l'application, page Système, carte"
  say "« LM Studio » : choisissez « Sur un autre ordinateur » et tapez son adresse."
  say "Pour installer LM Studio sur ce Mac plus tard : sh scripts/bootstrap.sh --avec-lm-studio"
  exit 0
fi

# The vision model to suggest: what fits in the unified memory next to Resolve and the rest.
MEMOIRE_GO=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1073741824 ))
if [ "$MEMOIRE_GO" -ge 32 ]; then
  MODELE="qwen/qwen3-vl-8b"
elif [ "$MEMOIRE_GO" -gt 8 ] || [ "$MEMOIRE_GO" -eq 0 ]; then
  MODELE="qwen/qwen3-vl-4b"
else
  MODELE="google/gemma-4-e2b" # about 4 GB: room left for the rest on an 8 GB Mac
fi
say "Terminé. Dans LM Studio, téléchargez un modèle de vision (par exemple $MODELE),"
say "chargez-le et activez le serveur local, puis double-cliquez sur run.command."
say "Le modèle peut aussi venir du LM Studio d'un autre ordinateur : donnez alors son adresse"
say "dans l'application, page Système, carte « LM Studio »."
if [ "$MEMOIRE_GO" -gt 0 ] && [ "$MEMOIRE_GO" -le 8 ]; then
  say "Avec $MEMOIRE_GO Go de mémoire, donnez au modèle un contexte court et fermez DaVinci Resolve"
  say "si le Mac ralentit pendant une analyse : tout partage la même mémoire."
fi
