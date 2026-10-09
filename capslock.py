"""Switch the Caps Lock function off in the desktop so the key can be the dictation hotkey.

Dictate only listens to the key, it does not consume it, so the desktop would still
toggle Caps Lock on every press and the typed text would come out in capitals. On GNOME
the keyboard option `caps:none` removes that function while the raw key events, which
the evdev listener reads, keep arriving.

Only `caps:none` is ever added or removed; other keyboard options stay as they are. It
is removed again only when this app added it (`managed`), never when the user had it.
"""
import ast
import shutil
import subprocess

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


def apply(wanted, managed, backend=None):
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
            backend.set(options + [OPTION])
            return True, ""
        if OPTION in options:
            backend.set([o for o in options if o != OPTION])
        return False, ""
    except Exception as e:
        return managed, f"Could not change the Caps Lock setting: {e}"
