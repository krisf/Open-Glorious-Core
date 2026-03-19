# Open Glorious Core

Open Glorious Core is an open-source Linux desktop app for Glorious peripherals.

Right now the project is focused on RGB control and has only been tested properly with the GMMK Pro, but the long-term goal is broader support for more Glorious keyboards, mice, and other peripherals.

## Current status

What works today:

- device detection on Linux through `hidraw`
- stock-firmware RGB control for the GMMK Pro
- solid colors
- breathing
- wave effects
- brightness control
- speed control
- custom colors with picker, RGB, and hex input
- native desktop window with an editable HTML/CSS/JS frontend

What is planned later:

- keybind and shortcut editing
- broader Glorious device support
- deeper effect mapping and firmware behavior cleanup

## Install

There are a few ways to install it depending on how you want to use it.

### Local user install

This installs the app into a private venv under your home directory and adds a launcher in `~/.local/bin`:

```sh
git clone https://github.com/your-username/gmmk-pro-linux-rgb.git
cd gmmk-pro-linux-rgb
./scripts/install.sh
```

After that, run:

```sh
gmmk-rgb
```

If `~/.local/bin` is not in your `PATH`, run:

```sh
~/.local/bin/gmmk-rgb
```

### Arch / pacman / AUR

Arch is the main distribution target for this project. The repo includes:

- `packaging/PKGBUILD`
- `packaging/.SRCINFO`
- `scripts/update-srcinfo.sh`

To build and install locally with Arch tools:

```sh
cp -r packaging /tmp/open-glorious-core-pkg
cd /tmp/open-glorious-core-pkg
makepkg -si
```

## Bugs and feedback

If something breaks, please open an issue on GitHub. Bug reports, hardware compatibility reports, and protocol notes are all useful.

## Editing the UI

The frontend is kept simple on purpose. You can edit it directly here:

- `src/gmmk_rgb_app/static/index.html`
- `src/gmmk_rgb_app/static/styles.css`
- `src/gmmk_rgb_app/static/app.js`
