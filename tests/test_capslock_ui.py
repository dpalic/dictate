"""Caps Lock checkbox in Settings, and applying it when the listener starts."""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def gui_module(qapp):
    try:
        import gui
    except Exception as e:   # no X display for pynput, no audio library, ...
        pytest.skip(f"gui cannot be imported here: {e}")
    return gui


# --- the checkbox -----------------------------------------------------------------

@pytest.fixture
def make_dialog(gui_module, monkeypatch):
    monkeypatch.setattr(gui_module.capslock, "is_available", lambda *a, **k: True)

    def make(**overrides):
        cfg = dict(gui_module.core.DEFAULT_CONFIG, provider="parakeet_local", **overrides)
        return gui_module.SettingsDialog(cfg)
    return make


def test_checkbox_is_hidden_for_other_hotkeys(make_dialog):
    dialog = make_dialog(key="f9")

    assert dialog.capslock_box.isHidden()


def test_checkbox_is_shown_and_checked_by_default_for_caps_lock(make_dialog):
    dialog = make_dialog(key="caps_lock")

    assert not dialog.capslock_box.isHidden()
    assert dialog.capslock_box.isChecked()
    assert dialog.values()["disable_capslock"] is True


def test_checkbox_follows_the_selected_hotkey(make_dialog):
    dialog = make_dialog(key="f9")

    dialog.key_box.setCurrentIndex(dialog.key_box.findData("caps_lock"))
    assert not dialog.capslock_box.isHidden()

    dialog.key_box.setCurrentIndex(dialog.key_box.findData("pause"))
    assert dialog.capslock_box.isHidden()


def test_unchecked_choice_is_kept(make_dialog):
    dialog = make_dialog(key="caps_lock", disable_capslock=False)

    assert not dialog.capslock_box.isChecked()
    assert dialog.values()["disable_capslock"] is False


def test_checkbox_sits_directly_below_the_hotkey_row(make_dialog):
    dialog = make_dialog(key="caps_lock")

    hotkey_row, _ = dialog.form.getWidgetPosition(dialog.key_box)
    checkbox_row, _ = dialog.form.getWidgetPosition(dialog.capslock_box)

    assert checkbox_row == hotkey_row + 1


def test_checkbox_is_disabled_without_gnome(gui_module, monkeypatch):
    monkeypatch.setattr(gui_module.capslock, "is_available", lambda *a, **k: False)
    cfg = dict(gui_module.core.DEFAULT_CONFIG, key="caps_lock")

    dialog = gui_module.SettingsDialog(cfg)

    assert not dialog.capslock_box.isEnabled()
    assert "GNOME" in dialog.capslock_box.toolTip()


# --- applying it when the listener starts -----------------------------------------

class Recorder:
    """Replaces capslock.apply and core.notify/save_config; records the calls."""

    def __init__(self, result=(False, "")):
        self.result = result
        self.applied, self.notified, self.saved = [], [], []

    def apply(self, wanted, managed, backend=None):
        self.applied.append((wanted, managed))
        return self.result

    def notify(self, msg, urgency="normal", timeout_ms=1500):
        self.notified.append(msg)

    def save_config(self, cfg):
        self.saved.append(dict(cfg))


@pytest.fixture
def make_window(gui_module, monkeypatch):
    evdev_listener = pytest.importorskip("evdev_listener")

    class FakeEvdev(evdev_listener.Listener):
        def stop(self):
            pass

    class FakePynput:
        def stop(self):
            pass

    windows = []

    def make(key, evdev, disable=True, managed=False, result=(False, "")):
        cfg = dict(gui_module.core.DEFAULT_CONFIG, key=key, disable_capslock=disable,
                   capslock_managed=managed)
        rec = Recorder(result)
        monkeypatch.setattr(gui_module.core, "load_config", lambda: dict(cfg))
        monkeypatch.setattr(gui_module.core, "save_config", rec.save_config)
        monkeypatch.setattr(gui_module.core, "notify", rec.notify)
        monkeypatch.setattr(gui_module.capslock, "apply", rec.apply)
        monkeypatch.setattr(gui_module, "_make_listener",
                            lambda on_press, on_release: FakeEvdev() if evdev else FakePynput())
        window = gui_module.MainWindow()
        windows.append(window)
        return window, rec

    yield make

    # A window that outlives its test must not fire its quit handler against the real desktop.
    from PyQt6.QtWidgets import QApplication
    for window in windows:
        try:
            QApplication.instance().aboutToQuit.disconnect(window._release_capslock)
        except TypeError:
            pass


def test_caps_lock_with_evdev_switches_the_function_off_and_remembers_it(make_window):
    window, rec = make_window("caps_lock", evdev=True, result=(True, ""))

    assert rec.applied == [(True, False)]
    assert rec.saved[-1]["capslock_managed"] is True
    assert rec.notified == []
    window.close()


def test_without_evdev_the_function_is_kept_and_the_user_is_told(make_window):
    window, rec = make_window("caps_lock", evdev=False)

    assert rec.applied == [(False, False)]           # hotkey would die with caps:none under pynput
    assert rec.saved == []
    assert len(rec.notified) == 1 and "log out and in" in rec.notified[0]
    window.close()


def test_unticked_box_leaves_caps_lock_alone(make_window):
    window, rec = make_window("caps_lock", evdev=True, disable=False)

    assert rec.applied == [(False, False)]
    assert rec.notified == []
    window.close()


def test_choosing_another_hotkey_gives_caps_lock_back(make_window):
    window, rec = make_window("f9", evdev=True, managed=True, result=(False, ""))

    assert rec.applied == [(False, True)]
    assert rec.saved[-1]["capslock_managed"] is False
    window.close()


def test_closing_the_app_gives_caps_lock_back(make_window):
    window, rec = make_window("caps_lock", evdev=True, managed=True, result=(True, ""))
    rec.applied.clear()
    rec.result = (False, "")      # what removing the option returns

    window._release_capslock()

    assert rec.applied == [(False, True)]
    assert rec.saved[-1]["capslock_managed"] is False
    window.close()


def test_closing_the_app_leaves_a_users_own_setting_alone(make_window):
    window, rec = make_window("caps_lock", evdev=True, managed=False, result=(False, ""))
    rec.applied.clear()
    rec.saved.clear()

    window._release_capslock()

    assert rec.applied == [(False, False)]        # apply() itself ignores what is not ours
    assert rec.saved == []
    window.close()


def test_a_failed_release_is_logged_and_ownership_is_kept(make_window, caplog):
    window, rec = make_window("caps_lock", evdev=True, managed=True, result=(True, "no dbus"))
    rec.saved.clear()

    with caplog.at_level("WARNING", logger="dictate"):
        window._release_capslock()

    assert rec.saved == []                         # still ours, so the next start can retry
    assert "no dbus" in caplog.text
    window.close()


def test_the_quit_signal_is_connected_to_the_release(gui_module, make_window, qapp):
    before = qapp.receivers(qapp.aboutToQuit)

    window, _ = make_window("f9", evdev=True)

    assert qapp.receivers(qapp.aboutToQuit) == before + 1
    window.close()


def test_failure_message_reaches_the_user(make_window):
    window, rec = make_window("caps_lock", evdev=True, result=(False, "Could not change the Caps Lock setting: no dbus"))

    assert rec.notified == ["Could not change the Caps Lock setting: no dbus"]
    window.close()
