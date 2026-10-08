#!/bin/sh
# Updates Video Frame Expedition for DaVinci Resolve in its folder, on a Mac: the latest version,
# then what it needs (install.sh run from the folder, with --update), then offers to start it in
# the same window. Started by update.command (double-click) or "Video Frame Expedition - update"
# in the Applications folder.
#
#   sh scripts/update.sh
#   sh scripts/update.sh --from <ZIP or folder>    (or --depuis)
#
# What is kept: the data folder (~/Library/Application Support/vfe-vision: the library's
# database with the preferences, the models, the media, the logs, the access token), the .env
# file, the application's environment and the interface's dependencies. The database is also
# backed up by the application itself before its structure changes, at the first start of the
# new version.
#
# A folder that comes from Git (the one-line installation) is updated with git pull. Otherwise
# the latest release is downloaded from GitHub and replaces the application's files; what the
# new version no longer has goes from the application's own folders (backend, frontend, scripts,
# docs), and nothing else of the folder is touched.
#
# --from installs that version instead (a ZIP downloaded from GitHub, or a folder): to try one
# before it is published. --no-install replaces the files only.
#
# The whole script is one block, read before it runs: the update replaces this file too.
{
set -eu
cd "$(dirname "$0")/.."
ROOT=$(pwd)
. scripts/language.sh
REPO="VideoFrameExpedition/video-frame-expedition-resolve"
PORT=8765

FROM=""
NO_INSTALL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --from | --depuis)
      [ $# -ge 2 ] || { say "--depuis attend un ZIP ou un dossier." "--from needs a ZIP or a folder."; exit 2; }
      FROM=$2
      shift
      ;;
    --no-install | --sans-installation) NO_INSTALL=1 ;;
    *)
      say "Option inconnue : $1 (options : --depuis, --sans-installation)" \
        "Unknown option: $1 (options: --from, --no-install)"
      exit 2
      ;;
  esac
  shift
done

# Homebrew's folders, for a Terminal opened by the Finder.
for dir in /opt/homebrew/bin /usr/local/bin "$HOME/.local/bin"; do
  case ":$PATH:" in
    *":$dir:"*) ;;
    *) [ -d "$dir" ] && PATH="$PATH:$dir" ;;
  esac
done
export PATH

fail() {
  echo
  say "[ERREUR] $1" "[ERROR] $2"
  exit 1
}

# The version of the application in a folder, from backend/pyproject.toml.
version_of() {
  sed -n 's/^version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$1/backend/pyproject.toml" \
    2>/dev/null | head -n 1
}

# newer A B: A is a later version than B.
newer() {
  awk -v a="$1" -v b="$2" 'BEGIN {
    n = split(a, x, "."); m = split(b, y, ".")
    for (i = 1; i <= (n > m ? n : m); i++) if (x[i] + 0 != y[i] + 0) exit !(x[i] + 0 > y[i] + 0)
    exit 1
  }'
}

lock_hash() {
  if [ -f frontend/pnpm-lock.yaml ]; then
    shasum -a 256 frontend/pnpm-lock.yaml | cut -d ' ' -f 1
  fi
}

# --- The application must be stopped: its files are in use while it runs. -------------------
while lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; do
  say "L'application tourne : fermez sa fenêtre (ou appuyez sur Ctrl+C dans celle-ci)." \
    "The application is running: close its window (or press Ctrl+C in it)."
  [ -t 0 ] || exit 1
  printf '%s' "$(say "Appuyez sur Entrée une fois l'application fermée (n : abandonner) " \
    "Press Return once the application is closed (n: give up) ")"
  read -r reponse || exit 1
  case "$reponse" in
    [nN]*) exit 1 ;;
  esac
done

INSTALLED=$(version_of "$ROOT")
say "Video Frame Expedition $INSTALLED, dans $ROOT" "Video Frame Expedition $INSTALLED, in $ROOT"
LOCK_BEFORE=$(lock_hash)
CHANGED=""
TEMPORARY=""

if [ -z "$FROM" ] && [ -d .git ]; then
  # A folder that comes from Git: git knows what changed.
  command -v git >/dev/null 2>&1 \
    || fail "ce dossier vient de Git, mais git est introuvable : lancez « xcode-select --install »." \
      "this folder comes from Git, but git cannot be found: run \"xcode-select --install\"."
  say "[..] Recherche d'une nouvelle version (git fetch)" "[..] Looking for a new version (git fetch)"
  git fetch --quiet || fail "git fetch a échoué (voir le message de git ci-dessus)." \
    "git fetch failed (see git's message above)."
  BEHIND=$(git rev-list --count 'HEAD..@{upstream}') \
    || fail "cette branche ne suit aucune branche de GitHub : mettez-la à jour avec git pull." \
      "this branch follows no branch of GitHub: update it with git pull."
  if [ "$BEHIND" -gt 0 ]; then
    git pull --ff-only || fail "git pull a échoué (voir le message de git ci-dessus)." \
      "git pull failed (see git's message above)."
    CHANGED=1
  fi
else
  TEMPORARY=$(mktemp -d "${TMPDIR:-/tmp}/vfe-update.XXXXXX")
  SOURCE=""
  if [ -n "$FROM" ]; then
    SOURCE=$FROM
  else
    say "[..] Recherche d'une nouvelle version sur GitHub" "[..] Looking for a new version on GitHub"
    # The latest release, from the address GitHub sends to: no API, so no limit on requests.
    LATEST_URL=$(curl -fsSLI -o /dev/null -w '%{url_effective}' \
      "https://github.com/$REPO/releases/latest") \
      || fail "GitHub ne répond pas : vérifiez la connexion à Internet." \
        "GitHub does not answer: check the Internet connection."
    case "$LATEST_URL" in
      */releases/tag/*) TAG=${LATEST_URL##*/} ;;
      *) fail "impossible de connaître la dernière version sur GitHub ($LATEST_URL)." \
        "cannot tell the latest version on GitHub ($LATEST_URL)." ;;
    esac
    LATEST=${TAG#v}
    if [ -z "$INSTALLED" ] || newer "$LATEST" "$INSTALLED"; then
      say "[..] Téléchargement de la version $LATEST" "[..] Downloading version $LATEST"
      SOURCE="$TEMPORARY/$TAG.zip"
      curl -fL --progress-bar -o "$SOURCE" "https://github.com/$REPO/archive/refs/tags/$TAG.zip" \
        || fail "le téléchargement a échoué." "the download failed."
    fi
  fi
  if [ -n "$SOURCE" ]; then
    if [ -f "$SOURCE" ]; then
      ditto -x -k "$SOURCE" "$TEMPORARY/unzipped" || fail "$SOURCE n'est pas un ZIP lisible." \
        "$SOURCE is not a readable ZIP."
      SOURCE="$TEMPORARY/unzipped"
    fi
    # GitHub puts everything in one folder named after the version.
    NEW=""
    for folder in "$SOURCE" "$SOURCE"/*/; do
      folder=${folder%/}
      if [ -n "$(version_of "$folder")" ] && [ -f "$folder/run.command" ]; then
        NEW=$(cd "$folder" && pwd)
        break
      fi
    done
    [ -n "$NEW" ] || fail "$SOURCE ne contient pas l'application (ni backend/pyproject.toml ni run.command)." \
      "$SOURCE does not hold the application (no backend/pyproject.toml nor run.command)."
    NEW_VERSION=$(version_of "$NEW")
    say "[..] Version $NEW_VERSION : remplacement des fichiers de l'application" \
      "[..] Version $NEW_VERSION: replacing the application's files"
    # Every file of the new version, then, in the application's own folders, what it no longer
    # has removed. Never removed, wherever they are: what is written next to the application's
    # files (its environment, the interface's dependencies and build, Python's caches) and
    # hidden files (.env). rsync writes each file anew: this script, being read, is not cut.
    rsync -a "$NEW/" "$ROOT/"
    for folder in "$NEW"/*/; do
      name=$(basename "$folder")
      rsync -a --delete --exclude '.*' --exclude 'node_modules' --exclude 'dist' \
        --exclude '__pycache__' --exclude '*.egg-info' "$NEW/$name/" "$ROOT/$name/"
    done
    xattr -dr com.apple.quarantine "$ROOT" 2>/dev/null || true
    CHANGED=1
  fi
  rm -rf "$TEMPORARY"
fi

if [ -z "$CHANGED" ]; then
  say "[ok] Vous avez déjà la dernière version ($INSTALLED)." \
    "[ok] You already have the latest version ($INSTALLED)."
  exit 0
fi

# New dependencies for the interface: installed again by run.command, at the next start.
if [ "$(lock_hash)" != "$LOCK_BEFORE" ] && [ -d frontend/node_modules ]; then
  say "[..] Les dépendances de l'interface ont changé : run.command les réinstallera" \
    "[..] The interface's dependencies changed: run.command will install them again"
  rm -rf frontend/node_modules
fi
chmod +x run.command install.command update.command 2>/dev/null || true

[ -z "$NO_INSTALL" ] || exit 0

# The new version's installation, from this folder (install.sh then bootstrap.sh, the new
# ones): its packages, its models, the Applications folder; what is already there is left as
# it is. It offers to start the application at the end.
exec /bin/sh "$ROOT/install.sh" --update
exit
}
