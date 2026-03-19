#!/usr/bin/env sh
set -eu

APP_ID="open-glorious-core"
INSTALL_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/$APP_ID"
BIN_DIR="${HOME}/.local/bin"
DESKTOP_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/applications/open-glorious-core.desktop"
ICON_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps/open-glorious-core.svg"

rm -f "$BIN_DIR/open-glorious-core" "$DESKTOP_FILE" "$ICON_FILE"
rm -rf "$INSTALL_ROOT"

echo "Removed Open Glorious Core from your user account."
