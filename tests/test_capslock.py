"""Switching the Caps Lock function off in the desktop, without touching a real desktop."""
import subprocess
from types import SimpleNamespace

import pytest

import capslock


class FakeBackend:
    """Stands in for gsettings; remembers every write."""

    def __init__(self, options=(), fail=None):
        self.options = list(options)
        self.fail = fail
        self.writes = []

    def get(self):
        if self.fail:
            raise self.fail
        return list(self.options)

    def set(self, options):
        if self.fail:
            raise self.fail
        self.options = list(options)
        self.writes.append(list(options))


# --- reading and writing the GVariant text -----------------------------------

@pytest.mark.parametrize("text, options", [
    ("@as []\n", []),
    ("[]", []),
    ("['caps:none']\n", ["caps:none"]),
    ("['grp:alt_shift_toggle', 'caps:none']", ["grp:alt_shift_toggle", "caps:none"]),
])
def test_parse_options(text, options):
    assert capslock.parse_options(text) == options


def test_format_options_round_trips():
    options = ["grp:alt_shift_toggle", "caps:none"]

    assert capslock.format_options(options) == "['grp:alt_shift_toggle', 'caps:none']"
    assert capslock.parse_options(capslock.format_options(options)) == options
    assert capslock.format_options([]) == "[]"


# --- apply ---------------------------------------------------------------------

def test_wanted_adds_the_option_and_takes_ownership():
    backend = FakeBackend(["grp:alt_shift_toggle"])

    managed, message = capslock.apply(True, False, backend)

    assert backend.options == ["grp:alt_shift_toggle", "caps:none"]
    assert (managed, message) == (True, "")


def test_wanted_leaves_a_users_own_setting_alone():
    backend = FakeBackend(["caps:none"])

    managed, message = capslock.apply(True, False, backend)

    assert backend.writes == []
    assert (managed, message) == (False, "")


def test_wanted_again_keeps_ownership_without_writing():
    backend = FakeBackend(["caps:none"])

    managed, _ = capslock.apply(True, True, backend)

    assert backend.writes == [] and managed is True


def test_not_wanted_removes_only_what_the_app_added():
    backend = FakeBackend(["grp:alt_shift_toggle", "caps:none"])

    managed, message = capslock.apply(False, True, backend)

    assert backend.options == ["grp:alt_shift_toggle"]
    assert (managed, message) == (False, "")


def test_not_wanted_never_removes_a_users_own_setting():
    backend = FakeBackend(["caps:none"])

    managed, message = capslock.apply(False, False, backend)

    assert backend.writes == [] and backend.options == ["caps:none"]
    assert (managed, message) == (False, "")


def test_not_wanted_and_not_managed_does_not_even_look_at_the_desktop():
    backend = FakeBackend(fail=RuntimeError("must not be called"))

    assert capslock.apply(False, False, backend) == (False, "")


def test_not_wanted_but_option_already_gone_just_forgets_ownership():
    backend = FakeBackend([])

    managed, _ = capslock.apply(False, True, backend)

    assert backend.writes == [] and managed is False


def test_failure_is_reported_and_ownership_stays_as_it_was():
    backend = FakeBackend(fail=OSError("no dbus"))

    managed, message = capslock.apply(True, False, backend)

    assert managed is False
    assert message == "Could not change the Caps Lock setting: no dbus"


# --- gsettings wrapper and availability ----------------------------------------

def test_gsettings_get_and_set_call_the_tool(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(stdout="['caps:none']\n")

    monkeypatch.setattr(capslock.subprocess, "run", fake_run)
    backend = capslock.Gsettings()

    assert backend.get() == ["caps:none"]
    backend.set(["a", "caps:none"])

    assert calls[0] == ["gsettings", "get", capslock.SCHEMA, capslock.KEY]
    assert calls[1] == ["gsettings", "set", capslock.SCHEMA, capslock.KEY, "['a', 'caps:none']"]


def test_available_when_the_setting_can_be_read():
    assert capslock.is_available(FakeBackend()) is True


def test_unavailable_when_reading_fails():
    assert capslock.is_available(FakeBackend(fail=subprocess.CalledProcessError(1, "gsettings"))) is False


def test_unavailable_without_the_gsettings_tool(monkeypatch):
    monkeypatch.setattr(capslock.shutil, "which", lambda name: None)

    assert capslock.is_available() is False
