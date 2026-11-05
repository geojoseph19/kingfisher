#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
# Build a Debian/Ubuntu package: dist/kingfisher_<version>_all.deb
#
#   packaging/build-deb.sh
#   MAINTAINER="Your Name <you@example.org>" packaging/build-deb.sh
#
# Needs only dpkg-deb (no root). The package installs to /usr and pulls in
# Tesseract, GTK 4, libadwaita and gnome-screenshot as dependencies.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG="kingfisher"
MAINTAINER="${MAINTAINER:-Geo Joseph <geojoseph19@users.noreply.github.com>}"
read -r APP_ID VERSION HOMEPAGE < <(cd "$ROOT" && python3 -c \
  "import kingfisher as k; print(k.APP_ID, k.VERSION, k.WEBSITE)")
# The release date comes from CHANGELOG.md ("## 1.0.0 - YYYY-MM-DD"), so
# rebuilding the same version never changes it.
DATE="$(sed -n "s/^## ${VERSION} - \([0-9-]*\)$/\1/p" "$ROOT/CHANGELOG.md" | head -n1)"
[[ -n "$DATE" ]] || { echo "CHANGELOG.md needs a '## $VERSION - YYYY-MM-DD' heading" >&2; exit 1; }

STAGE="$ROOT/build/deb/${PKG}_${VERSION}_all"
DIST="$ROOT/dist"
rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" "$STAGE/usr/bin" "$STAGE/usr/share/kingfisher" \
         "$STAGE/usr/share/applications" "$STAGE/usr/share/metainfo" \
         "$STAGE/usr/share/icons/hicolor/scalable/apps" "$STAGE/usr/share/doc/$PKG" "$DIST"

# Application code and data
cp -r "$ROOT/kingfisher" "$STAGE/usr/share/kingfisher/"
find "$STAGE/usr/share/kingfisher" \( -name '__pycache__' -o -name '*.pyc' \) -prune -exec rm -rf {} +
install -m 755 "$ROOT/bin/kingfisher" "$STAGE/usr/bin/kingfisher"

# Desktop integration
fill() { sed -e "s|@APP_ID@|$APP_ID|g" -e "s|@EXEC@|kingfisher|g" -e "s|@VERSION@|$VERSION|g" -e "s|@DATE@|$DATE|g" "$1"; }
fill "$ROOT/packaging/kingfisher.desktop.in" > "$STAGE/usr/share/applications/$APP_ID.desktop"
fill "$ROOT/packaging/kingfisher.metainfo.xml.in" > "$STAGE/usr/share/metainfo/$APP_ID.metainfo.xml"
cp "$ROOT/kingfisher/data/icons/hicolor/scalable/apps/kingfisher.svg" \
   "$STAGE/usr/share/icons/hicolor/scalable/apps/$APP_ID.svg"
for size in 16 24 32 48; do
  install -D -m 644 "$ROOT/kingfisher/data/icons/hicolor/${size}x${size}/apps/kingfisher.png" \
    "$STAGE/usr/share/icons/hicolor/${size}x${size}/apps/$APP_ID.png"
done

# Documentation
cp "$ROOT/README.md" "$STAGE/usr/share/doc/$PKG/README.md"
cp "$ROOT/TRADEMARKS.md" "$STAGE/usr/share/doc/$PKG/TRADEMARKS.md"
gzip -9n -c "$ROOT/CHANGELOG.md" > "$STAGE/usr/share/doc/$PKG/changelog.gz"
cat > "$STAGE/usr/share/doc/$PKG/copyright" <<EOF
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: Kingfisher

Files: *
Copyright: 2026 Geo Joseph
Comment: The Kingfisher name and logo are trademarks. As permitted by section
 7(e) of the GPL, the license does not grant rights to use them; see
 /usr/share/doc/kingfisher/TRADEMARKS.md.
License: GPL-3+
 This program is free software: you can redistribute it and/or modify
 it under the terms of the GNU General Public License as published by
 the Free Software Foundation, either version 3 of the License, or
 (at your option) any later version.
 .
 This program is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 GNU General Public License for more details.
 .
 You should have received a copy of the GNU General Public License
 along with this program.  If not, see <https://www.gnu.org/licenses/>.
 .
 On Debian systems, the complete text of the GNU General Public
 License version 3 can be found in "/usr/share/common-licenses/GPL-3".
EOF

# Permissions
find "$STAGE" -type d -exec chmod 755 {} +
find "$STAGE/usr/share" -type f -exec chmod 644 {} +

# Remove byte-code Python may have written if the app was ever run as root.
printf '%s\n' '#!/bin/sh' 'set -e' \
  "find /usr/share/kingfisher -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true" \
  > "$STAGE/DEBIAN/prerm"
chmod 755 "$STAGE/DEBIAN/prerm"

(cd "$STAGE" && find usr -type f -print0 | sort -z | xargs -0 md5sum) > "$STAGE/DEBIAN/md5sums"
chmod 644 "$STAGE/DEBIAN/md5sums"

INSTALLED_SIZE="$(du -sk --exclude=DEBIAN "$STAGE" | cut -f1)"
cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: graphics
Priority: optional
Architecture: all
Installed-Size: $INSTALLED_SIZE
Maintainer: $MAINTAINER
Homepage: $HOMEPAGE
Depends: python3 (>= 3.10), python3-gi, python3-gi-cairo, python3-cairo, python3-pil,
 gir1.2-gtk-4.0, gir1.2-adw-1 (>= 1.1), tesseract-ocr, tesseract-ocr-eng, gnome-screenshot,
 python3-pyzbar
Recommends: gir1.2-dbusmenu-glib-0.4, gnome-shell-extension-appindicator, gir1.2-packagekitglib-1.0
Suggests: tesseract-ocr-all
Description: Copy text from anything on your screen
 Kingfisher reads the text in anything you can see: a video, a photo, a
 scanned PDF, an error message. Press a shortcut, select the text, and it is
 on your clipboard. Everything happens on your computer.
 .
 Features precise area selection on any monitor, QR code and barcode
 reading, one-click links, email addresses and phone numbers, a choice
 between the original layout and paragraphs, a history of recent results,
 a keyboard shortcut and a top-bar icon.
EOF

OUT="$DIST/${PKG}_${VERSION}_all.deb"
dpkg-deb --root-owner-group -Zxz --build "$STAGE" "$OUT" >/dev/null
echo "Built $OUT"

# Validate when the tools are available; any problem fails the build.
if command -v desktop-file-validate >/dev/null; then
  desktop-file-validate "$STAGE/usr/share/applications/$APP_ID.desktop"
  echo "desktop file: OK"
fi
if command -v appstreamcli >/dev/null; then
  appstreamcli validate --no-net --pedantic "$STAGE/usr/share/metainfo/$APP_ID.metainfo.xml"
fi
