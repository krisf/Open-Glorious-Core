#!/usr/bin/env sh
set -eu

APP_ID="open-glorious-core"
APP_NAME="Open Glorious Core"
INSTALL_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/$APP_ID"
VENV_DIR="$INSTALL_ROOT/venv"
BIN_DIR="${HOME}/.local/bin"
APP_EXEC="$VENV_DIR/bin/open-glorious-core"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
DESKTOP_FILE="$DESKTOP_DIR/open-glorious-core.desktop"
ICON_NAME="open-glorious-core"
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

printf 'Installing %s into %s\n' "$APP_NAME" "$INSTALL_ROOT"
mkdir -p "$INSTALL_ROOT" "$BIN_DIR" "$DESKTOP_DIR" "$ICON_DIR"

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel
"$VENV_DIR/bin/python" -m pip install "$PROJECT_DIR"

ln -sfn "$APP_EXEC" "$BIN_DIR/open-glorious-core"
install -m 0644 "$PROJECT_DIR/src/open_glorious_core/static/assets/glorious-logomark.svg" "$ICON_DIR/$ICON_NAME.svg"
sed \
  -e "s|__APP_EXEC__|$APP_EXEC|g" \
  -e "s|__ICON_NAME__|$ICON_NAME|g" \
  "$PROJECT_DIR/packaging/open-glorious-core.desktop.in" > "$DESKTOP_FILE"
chmod 0644 "$DESKTOP_FILE"

cat <<MSG

Installed successfully.

Launcher:
  $BIN_DIR/open-glorious-core

Desktop entry:
  $DESKTOP_FILE

If ~/.local/bin is not in your PATH yet, add it and start a new shell.
For keyboard access you may still need to install the udev rule as root:
  sudo cp "$PROJECT_DIR/99-open-glorious-core-hid.rules" /etc/udev/rules.d/99-open-glorious-core-hid.rules
  sudo udevadm control --reload-rules
  sudo udevadm trigger
MSG
