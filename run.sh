#!/bin/bash
# Self-installing launcher for Dictate. Every step installs only what is missing:
#   1. system packages (apt, via sudo)
#   2. membership in group `input` (global hotkeys on Wayland)
#   3. uv
#   4. nvm, then Node lts/krypton (activated for the app)
#   5. ./.venv with the libraries from requirements.txt
#   6. the application-menu entry
#
#   ./run.sh           set up whatever is missing, then start the GUI
#   ./run.sh --setup   only set up
# Run it from a terminal the first time: apt needs your sudo password.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(pwd)"

# build-essential + python3-dev: evdev has no wheel and is compiled by pip.
APT_PACKAGES=(xdotool ydotool libnotify-bin libxcb-cursor0 libportaudio2 build-essential python3-dev)
UV_VERSION=0.12.24
NVM_VERSION=v0.40.3
NODE_LTS=lts/krypton        # Node 24
MIN_NODE_MAJOR=24
VENV=.venv
USER_NAME="${USER:-$(id -un)}"
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"

download() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$1"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- "$1"
    else
        echo "curl or wget is needed to download $1" >&2
        exit 1
    fi
}

ensure_system_packages() {
    if ! command -v dpkg-query >/dev/null 2>&1; then
        echo "Not a Debian/Ubuntu system: install these yourself: ${APT_PACKAGES[*]}" >&2
        return 0
    fi
    local missing=() p
    for p in "${APT_PACKAGES[@]}"; do
        dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || missing+=("$p")
    done
    if [ "${#missing[@]}" -gt 0 ]; then
        echo "Installing missing system packages: ${missing[*]}"
        sudo apt-get update -qq
        sudo apt-get install -y "${missing[@]}"
    fi
}

can_read_input() {
    local f
    for f in /dev/input/event*; do
        [ -r "$f" ] && return 0
    done
    return 1
}

# On Wayland the compositor does not forward global key events, so Dictate
# reads /dev/input/event* directly. Test that permission itself; only when it
# is missing, look at group `input` (`id -nG <name>` reads the group database,
# which changes right away, while the running session needs a new login).
ensure_input_group() {
    can_read_input && return 0
    # In the group, but the session is older: the app itself says so (and what to do)
    # when it fails to open the devices, right next to the fallback it switches to.
    if id -nG "$USER_NAME" | grep -qw input; then
        return 0
    fi

    cat <<EOF

Dictate needs permission to read your keyboard and mouse devices (/dev/input/event*).

  Why:     the dictation hotkey must work while another window has focus. On Wayland
           the desktop does not pass key presses to other programs, so Dictate reads
           the input devices itself.
  What:    add $USER_NAME to the system group 'input' (sudo usermod -a -G input $USER_NAME).
  Caution: every program running as you can then read all your keystrokes, not only
           Dictate. Agree only if you trust the software on this machine.
  Without: Dictate still runs. On X11 the hotkey keeps working through a fallback;
           on Wayland it may not react outside Dictate's own window.

EOF
    if [ ! -t 0 ]; then
        echo "Not asking (no terminal). Run ./run.sh from a terminal to grant it." >&2
        return 0
    fi
    local answer
    read -r -p "Add $USER_NAME to group 'input' now? [Y/n] " answer || answer=n
    case "${answer:-y}" in
        [Yy]*)
            sudo usermod -a -G input "$USER_NAME"
            cat <<EOF

Done. Linux applies group changes only to sessions started afterwards, so Dictate
keeps using its fallback for now (on X11 the hotkey still works, on Wayland it may
not react outside Dictate's own window).

Please log out and log in again (or reboot).

EOF
            ;;
        *)
            echo "Skipped. Run ./run.sh again if you change your mind."
            ;;
    esac
}

ensure_uv() {
    export PATH="$PATH:$HOME/.local/bin"   # where the uv installer puts it
    if ! command -v uv >/dev/null 2>&1; then
        echo "Installing uv $UV_VERSION ..."
        download "https://astral.sh/uv/$UV_VERSION/install.sh" | INSTALLER_NO_MODIFY_PATH=1 sh
    fi
}

# Active node/npm from nvm that is new enough: nothing to do.
node_is_ready() {
    [[ "$(command -v node 2>/dev/null || true)" == "$NVM_DIR"/* ]] &&
        command -v npm >/dev/null 2>&1 &&
        [ "$(node -p 'process.versions.node.split(".")[0]')" -ge "$MIN_NODE_MAJOR" ]
}

ensure_node() {
    node_is_ready && return 0

    if [ ! -s "$NVM_DIR/nvm.sh" ]; then
        echo "Installing nvm $NVM_VERSION into $NVM_DIR ..."
        # The installer aborts when NVM_DIR is set but missing.
        mkdir -p "$NVM_DIR"
        # PROFILE=/dev/null: leave ~/.bashrc alone; this script activates nvm itself.
        download "https://raw.githubusercontent.com/nvm-sh/nvm/$NVM_VERSION/install.sh" | PROFILE=/dev/null bash
    fi

    # nvm.sh is not written for `set -u`.
    set +u
    # shellcheck disable=SC1091
    . "$NVM_DIR/nvm.sh"
    if ! nvm use "$NODE_LTS" --silent >/dev/null 2>&1; then
        echo "Installing Node $NODE_LTS ..."
        nvm install "$NODE_LTS"
        nvm use "$NODE_LTS" --silent
    fi
    set -u
}

ensure_python_libs() {
    if [ ! -x "$VENV/bin/python" ]; then
        uv venv --python /usr/bin/python3 "$VENV"
    fi
    local missing=()
    mapfile -t missing < <("$VENV/bin/python" - requirements.txt <<'PY'
import importlib.metadata as md
import re
import sys

for line in open(sys.argv[1]):
    req = line.split("#")[0].strip()
    if not req:
        continue
    try:
        md.version(re.split(r"[<>=!~\[; ]", req, maxsplit=1)[0])
    except md.PackageNotFoundError:
        print(req)
PY
)
    if [ "${#missing[@]}" -gt 0 ]; then
        echo "Installing missing Python libraries: ${missing[*]}"
        uv pip install --python "$VENV/bin/python" "${missing[@]}"
    fi
}

# The menu entry starts run.sh, so it keeps working after dependency changes.
ensure_desktop_entry() {
    local dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
    local file="$dir/dictate.desktop" entry
    entry="[Desktop Entry]
Type=Application
Name=Dictate
GenericName=Voice dictation
Comment=Voice dictation to keyboard
Exec=\"$SCRIPT_DIR/run.sh\"
Path=$SCRIPT_DIR
Icon=audio-input-microphone
Terminal=false
Categories=Utility;Audio;Accessibility;
StartupNotify=false"
    if [ "$(cat "$file" 2>/dev/null || true)" != "$entry" ]; then
        mkdir -p "$dir"
        printf '%s\n' "$entry" > "$file"
        update-desktop-database "$dir" 2>/dev/null || true
        echo "Wrote menu entry $file"
    fi
}

ensure_system_packages
ensure_input_group
ensure_uv
ensure_node
ensure_python_libs
ensure_desktop_entry

if [ "${1:-}" = "--setup" ]; then
    exit 0
fi

exec "$VENV/bin/python" gui.py "$@"
