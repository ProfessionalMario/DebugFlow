"""
Headless unit tests for ChordWatcher.

These tests do NOT require a real pynput Listener (no display server, no
Windows-only hooks). We synthesise fake Key / KeyCode objects that mimic the
exact shapes pynput hands us in the wild — including the nasty Ctrl/Alt+letter
case where .char arrives as a control byte (e.g. '\\x06' for Ctrl+F).
"""

import sys
import os
import time
import types

# Insert src/ on path so this test runs without requiring `pip install -e .`.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


# ---------- Stub pynput so import always succeeds in this headless env ------
# flow_service guards `from pynput import keyboard as _pynput_kb` already, but
# without a stub the module sets _KEYBOARD_AVAILABLE = False and _canon_key
# short-circuits to None. We give it a minimal fake namespace it can talk to.
class _FakeKey:
    """Mimics pynput.keyboard.Key — has a .name attribute."""
    def __init__(self, name):
        self.name = name


class _FakeKeyCode:
    """Mimics pynput.keyboard.KeyCode — has .char and .vk."""
    def __init__(self, char=None, vk=None):
        self.char = char
        self.vk = vk


fake_kb = types.SimpleNamespace(Key=_FakeKey, KeyCode=_FakeKeyCode)
sys.modules.setdefault("pynput", types.ModuleType("pynput"))
sys.modules["pynput"].keyboard = fake_kb
sys.modules["pynput.keyboard"] = fake_kb

import importlib  # noqa: E402
import debugflow.flow_service as fs  # noqa: E402
importlib.reload(fs)


# Sanity: the module saw our fake pynput
assert fs._KEYBOARD_AVAILABLE, "Fake pynput stub failed to load — test setup broken."


# ---------- Helpers ---------------------------------------------------------
def _ctrl():  return _FakeKey("ctrl_l")
def _alt():   return _FakeKey("alt_l")
def _shift(): return _FakeKey("shift_l")

def _letter_clean(c):
    """The good case: Windows without modifiers, char arrives clean."""
    return _FakeKeyCode(char=c, vk=ord(c.upper()))

def _letter_ctrl_mangled(c):
    """The broken case: Ctrl held → char is control byte, vk still valid."""
    ctrl_byte = chr(ord(c.upper()) - 0x40)  # 'F' (0x46) → '\x06'
    return _FakeKeyCode(char=ctrl_byte, vk=ord(c.upper()))

def _letter_alt_mangled(c):
    """The other broken case: Alt held → char is None, vk still valid."""
    return _FakeKeyCode(char=None, vk=ord(c.upper()))


# ---------- Tests -----------------------------------------------------------
def test_canon_basic_letter():
    assert fs._canon_key(_letter_clean("f")) == "f"
    assert fs._canon_key(_letter_clean("s")) == "s"

def test_canon_modifiers():
    assert fs._canon_key(_ctrl())  == "ctrl"
    assert fs._canon_key(_alt())   == "alt"
    assert fs._canon_key(_shift()) == "shift"

def test_canon_ctrl_mangled_letter_falls_back_to_vk():
    """Regression: Ctrl+F arrives as char='\\x06'. Must canonicalise to 'f'."""
    k = _letter_ctrl_mangled("f")
    assert k.char == "\x06"             # confirm we built the gnarly case
    assert fs._canon_key(k) == "f"      # …and the canon function recovers

def test_canon_alt_mangled_letter_falls_back_to_vk():
    """Regression: Alt+S arrives as char=None. Must canonicalise to 's'."""
    k = _letter_alt_mangled("s")
    assert k.char is None
    assert fs._canon_key(k) == "s"

def test_canon_function_keys():
    assert fs._canon_key(_FakeKey("f5"))  == "f5"
    assert fs._canon_key(_FakeKey("f12")) == "f12"


def test_chord_watcher_fires_on_ctrl_alt_f_clean():
    fired = []
    cw = fs.ChordWatcher({"<ctrl>+<alt>+f": lambda: fired.append("hud")})

    cw._on_press(_ctrl())
    cw._on_press(_alt())
    cw._on_press(_letter_clean("f"))

    time.sleep(0.05)  # callback runs on a daemon thread
    assert fired == ["hud"], f"expected ['hud'], got {fired}"


def test_chord_watcher_fires_on_ctrl_alt_f_mangled():
    """The actual bug: Ctrl+Alt+F where F arrives as '\\x06'."""
    fired = []
    cw = fs.ChordWatcher({"<ctrl>+<alt>+f": lambda: fired.append("hud")})

    cw._on_press(_ctrl())
    cw._on_press(_alt())
    cw._on_press(_letter_ctrl_mangled("f"))   # the broken case

    time.sleep(0.05)
    assert fired == ["hud"], (
        "ChordWatcher dropped Ctrl+Alt+F because the F key arrived "
        "with a mangled control-byte char. vk fallback failed."
    )


def test_chord_watcher_does_not_fire_on_partial_press():
    """Ctrl+Alt alone must NOT fire a Ctrl+Alt+F chord."""
    fired = []
    cw = fs.ChordWatcher({"<ctrl>+<alt>+f": lambda: fired.append("hud")})

    cw._on_press(_ctrl())
    cw._on_press(_alt())
    # No F press — just the modifiers.

    time.sleep(0.05)
    assert fired == [], f"expected no fire, got {fired}"


def test_chord_watcher_does_not_fire_on_extra_key():
    """Ctrl+Alt+Shift+F must NOT fire the Ctrl+Alt+F chord (exact match)."""
    fired = []
    cw = fs.ChordWatcher({"<ctrl>+<alt>+f": lambda: fired.append("hud")})

    cw._on_press(_ctrl())
    cw._on_press(_alt())
    cw._on_press(_shift())
    cw._on_press(_letter_clean("f"))

    time.sleep(0.05)
    assert fired == [], (
        "Exact-match guard failed: Ctrl+Alt+Shift+F should not satisfy "
        "the Ctrl+Alt+F chord."
    )


def test_chord_watcher_routes_two_distinct_chords():
    """The two-chord interleave bug: HUD chord followed by trigger chord."""
    fired = []
    cw = fs.ChordWatcher({
        "<ctrl>+<alt>+f": lambda: fired.append("hud"),
        "<ctrl>+<alt>+s": lambda: fired.append("trigger"),
    })

    # 1) Ctrl+Alt+F  (HUD)
    cw._on_press(_ctrl())
    cw._on_press(_alt())
    cw._on_press(_letter_ctrl_mangled("f"))
    time.sleep(0.05)
    # Simulate OS dropping the F key-up because the HUD stole focus —
    # we deliberately do NOT call _on_release for F.

    # Wait past the per-chord cooldown so the second chord isn't blocked.
    time.sleep(fs.ChordWatcher({}).cooldown + 0.05)

    # 2) Ctrl+Alt+S  (trigger). Modifiers are still held.
    cw._on_press(_letter_ctrl_mangled("s"))
    time.sleep(0.05)

    assert fired == ["hud", "trigger"], (
        f"expected ['hud', 'trigger'], got {fired}. "
        "Stuck-letter clearance after fire is broken."
    )


def test_chord_watcher_autorepeat_is_ignored():
    fired = []
    cw = fs.ChordWatcher({"<ctrl>+<alt>+f": lambda: fired.append("hud")})

    cw._on_press(_ctrl())
    cw._on_press(_alt())
    cw._on_press(_letter_clean("f"))
    # OS autorepeat re-delivers F press without a release in between
    cw._on_press(_letter_clean("f"))
    cw._on_press(_letter_clean("f"))

    time.sleep(0.05)
    assert fired == ["hud"], f"expected single fire, got {fired}"


def test_chord_watcher_release_clears_state():
    fired = []
    cw = fs.ChordWatcher({"<ctrl>+<alt>+f": lambda: fired.append("hud")})

    cw._on_press(_ctrl())
    cw._on_press(_alt())
    cw._on_press(_letter_clean("f"))
    time.sleep(0.05)
    assert fired == ["hud"]

    # Full release of every key.
    cw._on_release(_ctrl())
    cw._on_release(_alt())
    cw._on_release(_letter_clean("f"))
    assert cw.pressed == set(), f"expected empty, got {cw.pressed}"


# ---------- Runner ----------------------------------------------------------
if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = []
    for t in tests:
        try:
            t()
            print(f"  ✔  {t.__name__}")
        except AssertionError as e:
            print(f"  ✖  {t.__name__}\n      {e}")
            failures.append(t.__name__)
        except Exception as e:
            print(f"  💥 {t.__name__}\n      {type(e).__name__}: {e}")
            failures.append(t.__name__)

    print()
    if failures:
        print(f"  FAILED: {len(failures)} / {len(tests)}")
        sys.exit(1)
    print(f"  OK: {len(tests)} / {len(tests)} passed")
