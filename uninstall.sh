#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
# Remove a per-user Kingfisher install.
#
#   ./uninstall.sh            remove the app (settings, history and models are kept)
#   ./uninstall.sh --purge    also remove settings, history and downloaded models
#
# For the .deb package use: sudo apt remove kingfisher
set -euo pipefail

PURGE=0
for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    -h|--help) sed -n '4,9p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg (see --help)" >&2; exit 2 ;;
  esac
done

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ID="$(cd "$SRC" && python3 -c 'import kingfisher; print(kingfisher.APP_ID)')"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"

if [[ -x "$HOME/.local/bin/kingfisher" ]]; then
  "$HOME/.local/bin/kingfisher" --disable-shortcut >/dev/null 2>&1 || true
fi
rm -f "$HOME/.local/bin/kingfisher"
rm -f "$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")/Kingfisher.desktop"
rm -f "$DATA_HOME/applications/$APP_ID.desktop"
rm -f "$DATA_HOME/icons/hicolor/scalable/apps/$APP_ID.svg"
for size in 16 24 32 48; do
  rm -f "$DATA_HOME/icons/hicolor/${size}x${size}/apps/$APP_ID.png"
done
rm -f "$CONFIG_HOME/autostart/$APP_ID.desktop"
rm -rf "$DATA_HOME/kingfisher/app"
update-desktop-database -q "$DATA_HOME/applications" 2>/dev/null || true

if ((PURGE)); then
  rm -rf "$DATA_HOME/kingfisher" "$CONFIG_HOME/kingfisher"
  echo "Kingfisher, its models and settings were removed."
else
  echo "Kingfisher was removed. Settings are kept in $CONFIG_HOME/kingfisher (use --purge to delete them)."
fi
