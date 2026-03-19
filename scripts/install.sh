#!/usr/bin/env sh
set -eu

APP_ID="gmmk-pro-linux-rgb"
APP_NAME="Open Glorious Core"
INSTALL_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/$APP_ID"
VENV_DIR="$INSTALL_ROOT/venv"
BIN_DIR="${HOME}/.local/bin"
APP_EXEC="$VENV_DIR/bin/gmmk-rgb"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
DESKTOP_FILE="$DESKTOP_DIR/gmmk-rgb.desktop"
ICON_NAME="gmmk-rgb"
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

printf 'Installing %s into %s\n' "$APP_NAME" "$INSTALL_ROOT"
mkdir -p "$INSTALL_ROOT" "$BIN_DIR" "$DESKTOP_DIR" "$ICON_DIR"

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel
"$VENV_DIR/bin/python" -m pip install "$PROJECT_DIR"

ln -sfn "$APP_EXEC" "$BIN_DIR/gmmk-rgb"
install -m 0644 "$PROJECT_DIR/src/gmmk_rgb_app/static/assets/glorious-logomark.svg" "$ICON_DIR/$ICON_NAME.svg"
sed \
  -e "s|__APP_EXEC__|$APP_EXEC|g" \
  -e "s|__ICON_NAME__|$ICON_NAME|g" \
  "$PROJECT_DIR/packaging/gmmk-rgb.desktop.in" > "$DESKTOP_FILE"
chmod 0644 "$DESKTOP_FILE"

cat <<MSG

Installed successfully.

Launcher:
  $BIN_DIR/gmmk-rgb

Desktop entry:
  $DESKTOP_FILE

If ~/.local/bin is not in your PATH yet, add it and start a new shell.
For keyboard access you may still need to install the udev rule as root:
  sudo cp "$PROJECT_DIR/99-gmmk-pro-qmk-hid.rules" /etc/udev/rules.d/99-gmmk-pro-qmk-hid.rules
  sudo udevadm control --reload-rules
  sudo udevadm trigger
MSG
