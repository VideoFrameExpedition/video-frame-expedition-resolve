#!/bin/bash
# Video Frame Expedition for DaVinci Resolve - macOS update (double-click in the Finder): the
# latest version in this folder (scripts/update.sh), keeping your library, your preferences, the
# models and the .env file; then it offers to start the application in this same window. The
# Applications folder has it too: "Video Frame Expedition - update". Its options are passed on:
# --from <ZIP or folder> installs that version instead (or --depuis).
cd "$(dirname "$0")" || exit 1
exec /bin/sh scripts/update.sh "$@"
