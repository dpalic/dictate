"""Switch the Caps Lock function off in the desktop so the key can be the dictation hotkey.

Dictate only listens to the key, it does not consume it, so the desktop would still
toggle Caps Lock on every press and the typed text would come out in capitals. On GNOME
the keyboard option `caps:none` removes that function while the raw key events, which
the evdev listener reads, keep arriving.

Only `caps:none` is ever added or removed; other keyboard options stay as they are. It
is removed again only when this app added it (`managed`), never when the user had it.

Caps Lock must be off at the moment the function is switched off: with `caps:none` the
key can no longer undo it, so a state left on would stay on. `apply` therefore switches
it off first, and does not touch the desktop if it cannot.
"""
import ast
import glob
import re
import shutil
import subprocess
import time

SCHEMA = "org.gnome.desktop.input-sources"
KEY = "xkb-options"
OPTION = "caps:none"


def parse_options(text):
    """GVariant text such as "@as []" or "['caps:none', 'grp:alt_shift_toggle']" -> list."""
    text = text.strip()
    if text.startswith("@as"):
        text = text[3:].strip()
    return [str(option) for option in ast.literal_eval(text)]


def format_options(options):
    return "[" + ", ".join(repr(option) for option in options) + "]"


class Gsettings:
    """The GNOME setting. Tests replace it."""

    def get(self):
        out = subprocess.run(["gsettings", "get", SCHEMA, KEY],
                             check=True, capture_output=True, text=True, timeout=5)
        return parse_options(out.stdout)

    def set(self, options):
        subprocess.run(["gsettings", "set", SCHEMA, KEY, format_options(options)],
                       check=True, capture_output=True, text=True, timeout=5)


LED_GLOB = "/sys/class/leds/*capslock*/brightness"
SETTLE_S = 1.0   # how long to wait for the state to change after a key press


class CapsLockState:
    """Whether Caps Lock is on, and a way to switch it off. Tests replace it."""

    def is_on(self):
        led_read = False
        for path in glob.glob(LED_GLOB):
            try:
                with open(path) as f:
                    value = int(f.read().strip() or 0)
            except (OSError, ValueError):
                continue
            led_read = True
            if value > 0:
                return True
        if led_read:
            return False
        # No readable keyboard LED: ask the X server instead.
        if shutil.which("xset"):
            try:
                out = subprocess.run(["xset", "q"], capture_output=True, text=True, timeout=3).stdout
            except Exception:
                return False
            return re.search(r"Caps Lock:\s+on", out) is not None
        return False

    def turn_off(self):
        """Press Caps Lock once and check that it worked. True when it is off now."""
        if not self.is_on():
            return True
        for cmd in (["xdotool", "key", "Caps_Lock"], ["ydotool", "key", "58:1", "58:0"]):
            if not shutil.which(cmd[0]):
                continue
            try:
                subprocess.run(cmd, capture_output=True, timeout=3)
            except Exception:
                continue
            deadline = time.monotonic() + SETTLE_S
            while time.monotonic() < deadline:
                if not self.is_on():
                    return True
                time.sleep(0.05)
        return not self.is_on()


def is_available(backend=None):
    """True when the option can be read, that is on GNOME with gsettings installed."""
    if backend is None:
        if not shutil.which("gsettings"):
            return False
        backend = Gsettings()
    try:
        backend.get()
    except Exception:
        return False
    return True


def apply(wanted, managed, backend=None, caps=None):
    """Make the desktop match `wanted`. Returns (managed, message).

    managed: True when this app added `caps:none` earlier, so it may remove it again.
    message: what to tell the user when something went wrong, otherwise "".
    """
    if not wanted and not managed:
        return False, ""
    backend = backend or Gsettings()
    try:
        options = backend.get()
        if wanted:
            if OPTION in options:
                return managed, ""   # already set; whoever set it keeps ownership
            caps = caps or CapsLockState()
            if not caps.turn_off():
                return managed, ("Caps Lock is on and could not be switched off, so its function "
                                 "stays enabled. Press Caps Lock once, then restart Dictate.")
            backend.set(options + [OPTION])
            return True, ""
        if OPTION in options:
            backend.set([o for o in options if o != OPTION])
        return False, ""
    except Exception as e:
        return managed, f"Could not change the Caps Lock setting: {e}"
