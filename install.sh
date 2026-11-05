#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
# Per-user install from source (no root needed except for missing apt packages).
# For a system-wide install, build and install the .deb instead (see README).
#
#   ./install.sh                 install into ~/.local (asks before installing apt packages)
#   ./install.sh --shortcut      also register Super+Shift+T as a system-wide shortcut
#   ./install.sh --best-models   also download the high-accuracy English model (~15 MB, once)
#   ./install.sh --desktop-icon  also put a launcher on the Desktop
#   ./install.sh --yes           don't prompt; install missing apt packages with sudo
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ID="$(cd "$SRC" && python3 -c 'import kingfisher; print(kingfisher.APP_ID)')"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
PREFIX="$DATA_HOME/kingfisher/app"
BIN_DIR="$HOME/.local/bin"
APPS_DIR="$DATA_HOME/applications"
ICON_DIR="$DATA_HOME/icons/hicolor/scalable/apps"
TESSDATA_DIR="$DATA_HOME/kingfisher/tessdata"

WANT_SHORTCUT=0 WANT_BEST=0 WANT_DESKTOP=0 ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --shortcut) WANT_SHORTCUT=1 ;;
    --best-models) WANT_BEST=1 ;;
    --desktop-icon) WANT_DESKTOP=1 ;;
    --yes|-y) ASSUME_YES=1 ;;
    -h|--help) sed -n '4,11p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }

bold "Checking dependencies"
missing=()    # required
optional=()   # nice to have; never blocks the install
python3 - <<'PY' 2>/dev/null || missing+=(python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-gi-cairo)
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); gi.require_foreign("cairo")
from gi.repository import Gtk, Adw
PY
python3 -c "import PIL" 2>/dev/null || missing+=(python3-pil)
command -v tesseract >/dev/null 2>&1 || missing+=(tesseract-ocr)
ls /usr/share/tesseract-ocr/*/tessdata/eng.traineddata >/dev/null 2>&1 || missing+=(tesseract-ocr-eng)
# Lets Kingfisher capture without GNOME's "Share this screenshot?" prompt.
command -v gnome-screenshot >/dev/null 2>&1 || missing+=(gnome-screenshot)
# QR code and barcode reading (small, so part of the standard install).
python3 -c "from pyzbar import pyzbar" 2>/dev/null || missing+=(python3-pyzbar)   # ~130 KB with libzbar
# Top-bar icon menu.
python3 -c "import gi; gi.require_version('Dbusmenu', '0.4')" 2>/dev/null || optional+=(gir1.2-dbusmenu-glib-0.4)
# One-click language packs (Languages window).
python3 -c "import gi; gi.require_version('PackageKitGlib', '1.0')" 2>/dev/null || optional+=(gir1.2-packagekitglib-1.0)

if ((${#missing[@]} + ${#optional[@]})); then
  ((${#missing[@]})) && warn "Missing packages: ${missing[*]}"
  ((${#optional[@]})) && warn "Optional packages not installed: ${optional[*]}"
  answer="n"
  if ((ASSUME_YES)); then
    answer="y"
  elif [[ -t 0 ]]; then
    read -r -p "  Install them now with sudo apt? [Y/n] " answer
    answer="${answer:-y}"
  fi
  if [[ "$answer" =~ ^[Yy] ]]; then
    sudo apt-get install -y "${missing[@]}" "${optional[@]}"
  elif ((${#missing[@]})); then
    echo "  Run this, then re-run the installer:"
    echo "    sudo apt install ${missing[*]} ${optional[*]}"
    exit 1
  else
    echo "  Continuing without them. Install later with: sudo apt install ${optional[*]}"
  fi
fi
ok "Required dependencies present"

bold "Installing Kingfisher"
rm -rf "$PREFIX"
mkdir -p "$PREFIX" "$BIN_DIR" "$APPS_DIR" "$ICON_DIR"
cp -r "$SRC/kingfisher" "$SRC/bin" "$PREFIX/"
install -m 755 "$SRC/uninstall.sh" "$PREFIX/uninstall.sh"   # so removal never needs the source
find "$PREFIX" \( -name '__pycache__' -o -name '*.pyc' \) -prune -exec rm -rf {} +
chmod +x "$PREFIX/bin/kingfisher"
ln -sf "$PREFIX/bin/kingfisher" "$BIN_DIR/kingfisher"
ok "App files in $PREFIX"

cp "$SRC/kingfisher/data/icons/hicolor/scalable/apps/kingfisher.svg" "$ICON_DIR/$APP_ID.svg"
# Hand-tuned small sizes (bolder dashes) for menus, the dock and the window list.
for size in 16 24 32 48; do
  mkdir -p "$DATA_HOME/icons/hicolor/${size}x${size}/apps"
  cp "$SRC/kingfisher/data/icons/hicolor/${size}x${size}/apps/kingfisher.png" \
     "$DATA_HOME/icons/hicolor/${size}x${size}/apps/$APP_ID.png"
done
gtk-update-icon-cache -q -t "$DATA_HOME/icons/hicolor" 2>/dev/null || true

# Render the launcher with the app's own desktop-entry quoting, so any
# install path (spaces, quotes, $, %, \) produces a valid Exec line.
(cd "$SRC" && python3 - "$BIN_DIR/kingfisher" "$APP_ID" \
    "$SRC/packaging/kingfisher.desktop.in" "$APPS_DIR/$APP_ID.desktop" <<'PY'
import sys
from kingfisher.autostart import exec_value
launcher, app_id, template, target = sys.argv[1:]
text = open(template, encoding="utf-8").read()
text = text.replace("@APP_ID@", app_id).replace("@EXEC@", exec_value([launcher]))
open(target, "w", encoding="utf-8").write(text)
PY
)
update-desktop-database -q "$APPS_DIR" 2>/dev/null || true
ok "Launcher added to the app grid"

if ((WANT_DESKTOP)); then
  DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
  if [[ -d "$DESKTOP_DIR" ]]; then
    install -m 755 "$APPS_DIR/$APP_ID.desktop" "$DESKTOP_DIR/Kingfisher.desktop"
    # Mark trusted so the Ubuntu desktop shows it as launchable.
    gio set "$DESKTOP_DIR/Kingfisher.desktop" metadata::trusted true 2>/dev/null || true
    ok "Desktop icon created"
  else
    warn "No Desktop folder found; skipped desktop icon"
  fi
fi

if ((WANT_BEST)); then
  bold "Downloading the high-accuracy English model (one time)"
  mkdir -p "$TESSDATA_DIR"
  # Pinned release and checksum, so the file is exactly the one tested.
  url="https://github.com/tesseract-ocr/tessdata_best/raw/4.1.0/eng.traineddata"
  sha256="8280aed0782fe27257a68ea10fe7ef324ca0f8d85bd2fd145d1c2b560bcb66ba"
  part="$TESSDATA_DIR/eng.traineddata.part"
  if command -v curl >/dev/null; then
    fetch=(curl -fL --progress-bar -o "$part" "$url")
  elif command -v wget >/dev/null; then
    fetch=(wget -q --show-progress -O "$part" "$url")
  else
    fetch=()
    warn "Neither curl nor wget is installed (sudo apt install curl); skipped the model"
  fi
  if ((${#fetch[@]})) && "${fetch[@]}" && echo "$sha256  $part" | sha256sum -c --status; then
    mv "$part" "$TESSDATA_DIR/eng.traineddata"
    ok "Saved. Choose it in Preferences › Recognition model"
  elif ((${#fetch[@]})); then
    rm -f "$TESSDATA_DIR/eng.traineddata.part"
    warn "Download failed; the standard model is still used"
  fi
fi

if ((WANT_SHORTCUT)); then
  "$BIN_DIR/kingfisher" --enable-shortcut && ok "System-wide shortcut registered"
fi

echo
bold "Done."
echo "  Open Kingfisher from the app grid, or run: kingfisher"
echo "  Quick capture from anywhere:            kingfisher --capture"
((WANT_SHORTCUT)) || echo "  Set a global shortcut in Kingfisher › Preferences, or re-run with --shortcut"
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) warn "$BIN_DIR is not on your PATH (the app grid launcher still works)" ;; esac
