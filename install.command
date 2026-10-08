#!/bin/bash
# Video Frame Expedition for DaVinci Resolve - macOS installer (double-click in the Finder), for
# the application's folder downloaded as a ZIP or with git clone: installs what is missing
# (Homebrew, uv, Node.js, FFmpeg, ExifTool, the Python packages and the models; LM Studio when
# wanted) and adds "Video Frame Expedition" to the Applications folder. It runs install.sh from
# this folder, which downloads nothing again; the options of scripts/bootstrap.sh are passed on
# (--no-models, --with-lm-studio, --without-lm-studio).
cd "$(dirname "$0")" || exit 1
exec /bin/sh ./install.sh "$@"
