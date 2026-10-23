import pytest

import win_hotkeys as hk


def test_default_hotkey_parses(qapp):
    mods, vk = hk.parse_hotkey("Ctrl+Alt+P")
    assert mods == hk.MOD_CONTROL | hk.MOD_ALT and vk == ord("P")
    assert hk.normalise("ctrl+alt+p") == "Ctrl+Alt+P"


def test_single_modifier_rejected(qapp):
    with pytest.raises(ValueError):
        hk.parse_hotkey("Ctrl+P")


def test_manager_apply_and_duplicates(qapp):
    m = hk.HotkeyManager()
    # an unusual combination, so a running Process Radar (which owns Ctrl+Alt+P) does not interfere
    errs = m.apply({"toggle": "Ctrl+Alt+Shift+F11", "show": "Ctrl+Alt+Shift+F11", "hide": ""})
    assert m.active == {"toggle": "Ctrl+Alt+Shift+F11"}
    assert errs and errs[0][0] == "show"
    m.unregister_all()
