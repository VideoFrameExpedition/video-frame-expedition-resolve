#!/bin/sh
# Installs Video Frame Expedition for DaVinci Resolve on a Mac (Apple Silicon, macOS 15 or
# later): the missing programs (through Homebrew), the application's Python packages, then its
# models.
#
#   sh scripts/bootstrap.sh
#   sh scripts/bootstrap.sh --no-models          # without the application's models
#   sh scripts/bootstrap.sh --with-lm-studio     # install LM Studio without asking
#   sh scripts/bootstrap.sh --without-lm-studio  # leave LM Studio out without asking
#   sh scripts/bootstrap.sh --update             # for scripts/update.sh: no question, short end
#
# The French spellings --sans-modeles, --avec-lm-studio, --sans-lm-studio and --mise-a-jour work
# too.
#
# LM Studio, which runs the vision model, is installed only when the person running the script
# wants it: the question is asked unless it was answered in advance, and LM Studio is left out
# when nobody is at the keyboard. The vision model can also come from the LM Studio of another
# computer (System page of the application).
#
# Safe to run again: what is already there is left as it is. Neither pnpm nor just is needed:
# run.command builds the interface with Node only. The messages on screen are in English, or in
# French on a Mac set to French (scripts/language.sh).
set -eu
cd "$(dirname "$0")/.."
. scripts/language.sh

SANS_MODELES=""
LM_STUDIO="" # oui or non: the answer given in advance
MISE_A_JOUR=""
for arg in "$@"; do
  case "$arg" in
    --no-models | --sans-modeles) SANS_MODELES=1 ;;
    --update | --mise-a-jour) MISE_A_JOUR=1 ;;
    --with-lm-studio | --avec-lm-studio | --without-lm-studio | --sans-lm-studio)
      choice=oui
      case "$arg" in
        --without-lm-studio | --sans-lm-studio) choice=non ;;
      esac
      if [ -n "$LM_STUDIO" ] && [ "$LM_STUDIO" != "$choice" ]; then
        say "Choisissez --avec-lm-studio ou --sans-lm-studio, pas les deux." \
          "Choose --with-lm-studio or --without-lm-studio, not both."
        exit 2
      fi
      LM_STUDIO=$choice
      ;;
    *)
      say "Option inconnue : $arg (options : --sans-modeles, --avec-lm-studio, --sans-lm-studio)" \
        "Unknown option: $arg (options: --no-models, --with-lm-studio, --without-lm-studio)"
      exit 2
      ;;
  esac
done
# An update asks nothing: LM Studio is left as it is.
if [ -n "$MISE_A_JOUR" ] && [ -z "$LM_STUDIO" ]; then
  LM_STUDIO=non
fi

if [ "$(uname -s)" != "Darwin" ]; then
  say "Ce script installe l'application sur un Mac." "This script installs the application on a Mac."
  exit 1
fi
if [ "$(uname -m)" != "arm64" ]; then
  say "[ATTENTION] Ce Mac n'a pas de puce Apple Silicon : LM Studio et DaVinci Resolve 21 ne" \
    "[WARNING] This Mac has no Apple Silicon chip: LM Studio and DaVinci Resolve 21 do not run"
  say "            tournent pas dessus, et les paquets Python de l'application non plus." \
    "          on it, and neither do the application's Python packages."
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
  say "[..] Installation de Homebrew (https://brew.sh) : votre mot de passe sera demandé" \
    "[..] Installing Homebrew (https://brew.sh): your password will be asked"
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
    say "[ok] $1" "[ok] $1"
    return
  fi
  say "[..] Installation de $1 (brew install ${4:-} $3)" "[..] Installing $1 (brew install ${4:-} $3)"
  # shellcheck disable=SC2086 # ${4:-} is empty or the single word --cask
  brew install ${4:-} "$3"
}

install_if_missing "uv" "command -v uv" "uv"
install_if_missing "Node.js" "command -v node" "node"
# FFmpeg's full build: its zscale filter turns HDR footage into the frames the vision model is
# shown. Keg-only, it stays out of the PATH; the application finds it in its own folder.
install_if_missing "$(say "FFmpeg (version complète)" "FFmpeg (full build)")" \
  "test -x /opt/homebrew/opt/ffmpeg-full/bin/ffmpeg || test -x /usr/local/opt/ffmpeg-full/bin/ffmpeg" \
  "ffmpeg-full"
install_if_missing "ExifTool" "command -v exiftool" "exiftool"
LM_STUDIO_APP="/Applications/LM Studio.app"
if [ -d "$LM_STUDIO_APP" ]; then
  say "[ok] LM Studio" "[ok] LM Studio"
else
  if [ -z "$LM_STUDIO" ]; then
    LM_STUDIO=non
    if [ -t 0 ]; then
      echo
      say "LM Studio fait tourner le modèle de vision. Il peut être installé sur ce Mac, ou rester" \
        "LM Studio runs the vision model. It can be installed on this Mac, or stay on another"
      say "sur un autre ordinateur du réseau (son adresse se donne dans l'application, page Système)." \
        "computer of the network (its address is given in the application, System page)."
      printf '%s' "$(say "Installer LM Studio sur ce Mac ? [o/N] " "Install LM Studio on this Mac? [y/N] ")"
      read -r reponse || reponse=""
      case "$reponse" in
        [oOyY]*) LM_STUDIO=oui ;;
      esac
    fi
  fi
  if [ "$LM_STUDIO" = oui ]; then
    say "[..] Installation de LM Studio (brew install --cask lm-studio)" \
      "[..] Installing LM Studio (brew install --cask lm-studio)"
    brew install --cask lm-studio
  else
    say "[--] LM Studio n'est pas installé sur ce Mac" "[--] LM Studio is not installed on this Mac"
  fi
fi

# Exactly the versions of uv.lock (--locked), without the development tools (--no-dev);
# --inexact leaves alone what a developer installed on top.
say "[..] Paquets Python de l'application" "[..] The application's Python packages"
uv sync --locked --no-dev --inexact --project backend

if [ -z "$SANS_MODELES" ]; then
  # Offline places, sounds, speech and on-screen text, subjects, search by meaning: ~2 GB.
  for pack in geonames audio-text subjects search; do
    say "[..] Modèles : $pack" "[..] Models: $pack"
    uv run --frozen --no-dev --project backend python -m vfe_vision models "$pack"
  done
fi

chmod +x run.command install.command update.command 2>/dev/null || true
echo

if [ -n "$MISE_A_JOUR" ]; then
  say "Mise à jour terminée : votre bibliothèque, vos réglages et les modèles sont restés en place." \
    "Update complete: your library, your settings and the models stayed in place."
  exit 0
fi

# How to start the application: what the one-line installer (install.sh) added to the
# Applications folder, otherwise run.command.
if [ -n "${VFE_LAUNCHER:-}" ]; then
  START_FR="ouvrez « $VFE_LAUNCHER » (Launchpad, Spotlight ou dossier Applications)"
  START_EN="open \"$VFE_LAUNCHER\" (Launchpad, Spotlight or the Applications folder)"
else
  START_FR="double-cliquez sur run.command"
  START_EN="double-click run.command"
fi

if [ ! -d "$LM_STUDIO_APP" ]; then
  say "Terminé. Sur l'ordinateur qui a LM Studio, chargez un modèle de vision et laissez son" \
    "Done. On the computer that has LM Studio, load a vision model and let its server accept"
  say "serveur accepter le réseau local (Developer › Server Settings › « Serve on Local Network »)." \
    "the local network (Developer › Server Settings › \"Serve on Local Network\")."
  say "Ensuite, $START_FR ; dans l'application, page Système, carte" \
    "Then $START_EN; in the application, System page, \"LM Studio\" card:"
  say "« LM Studio » : choisissez « Sur un autre ordinateur » et tapez son adresse." \
    "choose \"On another computer\" and type its address."
  say "Pour installer LM Studio sur ce Mac plus tard : sh scripts/bootstrap.sh --avec-lm-studio" \
    "To install LM Studio on this Mac later: sh scripts/bootstrap.sh --with-lm-studio"
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
say "Terminé. Dans LM Studio, téléchargez un modèle de vision (par exemple $MODELE)," \
  "Done. In LM Studio, download a vision model (for example $MODELE),"
say "chargez-le et activez le serveur local, puis $START_FR." \
  "load it and start the local server, then $START_EN."
say "Le modèle peut aussi venir du LM Studio d'un autre ordinateur : donnez alors son adresse" \
  "The model can also come from the LM Studio of another computer: then give its address"
say "dans l'application, page Système, carte « LM Studio »." \
  "in the application, System page, \"LM Studio\" card."
if [ "$MEMOIRE_GO" -gt 0 ] && [ "$MEMOIRE_GO" -le 8 ]; then
  say "Avec $MEMOIRE_GO Go de mémoire, donnez au modèle un contexte court et fermez DaVinci Resolve" \
    "With $MEMOIRE_GO GB of memory, give the model a short context and close DaVinci Resolve"
  say "si le Mac ralentit pendant une analyse : tout partage la même mémoire." \
    "if the Mac slows down during an analysis: everything shares the same memory."
fi
