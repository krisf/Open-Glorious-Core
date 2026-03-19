#!/usr/bin/env sh
set -eu

APP_ID="gmmk-pro-linux-rgb"
INSTALL_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/$APP_ID"
BIN_DIR="${HOME}/.local/bin"
DESKTOP_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/applications/gmmk-rgb.desktop"
ICON_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps/gmmk-rgb.svg"

rm -f "$BIN_DIR/gmmk-rgb" "$DESKTOP_FILE" "$ICON_FILE"
rm -rf "$INSTALL_ROOT"

echo "Removed Open Glorious Core from your user account."
