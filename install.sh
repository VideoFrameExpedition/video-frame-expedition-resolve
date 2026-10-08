#!/bin/sh
# Installs Video Frame Expedition for DaVinci Resolve on a Mac, in one line typed in the
# Terminal:
#
#   /bin/sh -c "$(curl -fsSL https://raw.githubusercontent.com/VideoFrameExpedition/video-frame-expedition-resolve/main/install.sh)"
#
# It gets the application (git clone into ~/video-frame-expedition-resolve; git pull when it is
# already there, so the same line updates it), installs what is missing (scripts/bootstrap.sh:
# Homebrew, uv, Node.js, FFmpeg, ExifTool, the Python packages and the models; LM Studio when
# wanted), then adds "Video Frame Expedition" to the user's Applications folder: opened from
# Launchpad, Spotlight or the Dock, it starts run.command in the Terminal. Nothing downloaded
# through a browser, so nothing for Gatekeeper to stop.
#
# Run as a file from the application's folder (double-click on install.command, or
# sh install.sh there), it installs that folder as it is, downloaded as a ZIP or with git clone:
# nothing is downloaded again, and the quarantine mark of a ZIP is removed.
#
# VFE_DIR chooses another folder; VFE_REPO another repository (a URL, or a local folder to try
# a version before it is published). The options of scripts/bootstrap.sh are passed on:
#   /bin/sh -c "$(curl -fsSL …/install.sh)" install.sh --without-lm-studio
set -eu

REPO="${VFE_REPO:-https://github.com/VideoFrameExpedition/video-frame-expedition-resolve.git}"
DIR="${VFE_DIR:-$HOME/video-frame-expedition-resolve}"
NAME="Video Frame Expedition"
APP="$HOME/Applications/$NAME.app"

# Typed as one line (sh -c), $0 is the shell; run as a file, it is this script, in the
# application's folder.
HERE=""
case "$0" in
  *install.sh)
    HERE=$(cd "$(dirname "$0")" && pwd)
    [ -f "$HERE/scripts/bootstrap.sh" ] && DIR=$HERE || HERE=""
    ;;
esac

# The language of the messages, as scripts/language.sh decides it (not downloaded yet):
# VFE_LANG, otherwise the Mac's first preferred language, then the locale.
if [ -z "${VFE_LANG:-}" ] && command -v defaults >/dev/null 2>&1; then
  VFE_LANG=$(defaults read -g AppleLanguages 2>/dev/null | sed -n '2p' | tr -d "\" ,")
fi
case "${VFE_LANG:-${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}}" in
  fr* | FR*) VFE_LANG=fr ;;
  *) VFE_LANG=en ;;
esac
export VFE_LANG

# One line in the language of the messages: the first text in French, the second in English.
say() {
  if [ "$VFE_LANG" = fr ]; then
    printf '%s\n' "$1"
  else
    printf '%s\n' "$2"
  fi
}

fail() {
  echo
  say "[ERREUR] $1" "[ERROR] $2"
  exit 1
}

if [ "$(uname -s)" != "Darwin" ]; then
  say "Ce script installe l'application sur un Mac ; sous Windows, voir le README." \
    "This script installs the application on a Mac; on Windows, see the README."
  exit 1
fi

# Homebrew's folders, for a Terminal that does not have them in its PATH yet.
for dir in /opt/homebrew/bin /usr/local/bin; do
  case ":$PATH:" in
    *":$dir:"*) ;;
    *) [ -d "$dir" ] && PATH="$PATH:$dir" ;;
  esac
done
export PATH

# Git comes with Apple's command line tools, which Homebrew installs: Homebrew first, when the
# Mac does not have it (scripts/bootstrap.sh would install it too, but after git is needed).
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

if [ -n "$HERE" ]; then
  say "[..] Installation depuis ce dossier : $DIR" "[..] Installing from this folder: $DIR"
  # A folder downloaded as a ZIP carries a quarantine mark: without it, run.command opens with
  # a double-click.
  xattr -dr com.apple.quarantine "$DIR" 2>/dev/null || true
elif ! xcode-select -p >/dev/null 2>&1; then
  fail "les outils en ligne de commande d'Apple (git) manquent : lancez « xcode-select --install », puis cette ligne à nouveau." \
    "Apple's command line tools (git) are missing: run \"xcode-select --install\", then this line again."
elif [ -d "$DIR/.git" ]; then
  say "[..] Mise à jour de l'application : $DIR" "[..] Updating the application: $DIR"
  git -C "$DIR" pull --ff-only \
    || fail "la mise à jour de $DIR a échoué : voir le message de git ci-dessus." \
      "the update of $DIR failed: see git's message above."
elif [ -e "$DIR" ]; then
  fail "$DIR existe déjà sans venir de Git : renommez-le, ou choisissez un autre dossier avec VFE_DIR." \
    "$DIR already exists and does not come from Git: rename it, or choose another folder with VFE_DIR."
else
  say "[..] Téléchargement de l'application dans $DIR" "[..] Downloading the application into $DIR"
  git clone "$REPO" "$DIR"
fi

# The application in the Applications folder: an AppleScript applet made on this Mac, which
# opens run.command in the Terminal as a double-click would, with the application's icon.
say "[..] « $NAME » dans le dossier Applications" "[..] \"$NAME\" in the Applications folder"
mkdir -p "$HOME/Applications"
rm -rf "$APP"
osacompile -o "$APP" \
  -e "do shell script \"open -a Terminal \" & quoted form of \"$DIR/run.command\""
if sips -s format icns "$DIR/docs/brand/icons/icon-512.png" \
  --out "$APP/Contents/Resources/applet.icns" >/dev/null 2>&1; then
  # osacompile also puts the default icon in an asset catalogue, which macOS prefers
  # (CFBundleIconName): without it, the icon file is the one shown.
  rm -f "$APP/Contents/Resources/Assets.car"
  /usr/libexec/PlistBuddy -c "Delete :CFBundleIconName" "$APP/Contents/Info.plist" \
    >/dev/null 2>&1 || true
  codesign --force --sign - "$APP" >/dev/null 2>&1 || true # sealed again with its icon
  touch "$APP"
fi

VFE_LAUNCHER="$NAME" sh "$DIR/scripts/bootstrap.sh" "$@"

echo
if [ -z "$HERE" ]; then
  say "Pour mettre l'application à jour plus tard, relancez la même ligne dans le Terminal." \
    "To update the application later, run the same line in the Terminal again."
fi
# Started now, the application runs in this same window (run.command takes over; Ctrl+C stops
# it). Not asked when run.command started the installation: it goes on itself.
if [ -t 0 ] && [ -z "${VFE_NO_OPEN:-}" ]; then
  printf '%s' "$(say "Démarrer l'application maintenant, dans cette fenêtre ? [O/n] " \
    "Start the application now, in this window? [Y/n] ")"
  read -r reponse || reponse=n
  case "$reponse" in
    [nN]*)
      say "Pour la démarrer plus tard : ouvrez « $NAME » (Launchpad, Spotlight ou dossier Applications)." \
        "To start it later: open \"$NAME\" (Launchpad, Spotlight or the Applications folder)."
      ;;
    *) exec /bin/bash "$DIR/run.command" ;;
  esac
fi
