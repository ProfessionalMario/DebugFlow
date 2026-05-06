"""
File summary: Summary unavailable due to analysis error.
"""

import sys
import os
import time
import threading
import socket
import psutil
import traceback
import uuid
import platform
import subprocess

try:
    from . import log
except ImportError:
    import logging
    log = logging.getLogger("debugflow")

try:
    from pynput import keyboard as _pynput_kb
    _KEYBOARD_AVAILABLE = True
except Exception:
    _KEYBOARD_AVAILABLE = False


# --- CHORD WATCHER -----------------------------------------------------------
# Replacement for pynput.GlobalHotKeys.
#
# pynput.GlobalHotKeys tracks press/release internally. On Windows, when a
# focused window consumes a key (e.g. VS Code eating Ctrl+S, or the HUD
# spawning and stealing focus on Ctrl+Alt+F), pynput's hook receives the
# press but the matching release is swallowed by the focused window. The
# letter key ("F" / "S") then stays "stuck" in pynput's internal state, and
# the very next press of Ctrl+Alt instantly satisfies the stale chord —
# producing the symptom the user reported: HUD toggling on Ctrl+Alt alone,
# Ctrl+Alt+S re-toggling the HUD instead of firing the trigger.
#
# This watcher rebuilds the press-set ourselves from raw key events and
# fires a chord only when:
#   1. The pressed set EXACTLY equals the chord (no extra keys, no missing
#      keys). Subset matching would let a stuck letter from a prior chord
#      bleed into the next one.
#   2. The press is not an autorepeat.
#   3. Per-chord cooldown (0.4s) hasn't fired yet.
#
# IMPORTANT: after firing, we proactively remove the non-modifier ("trigger")
# keys of the chord from our internal pressed set. That's the actual cure for
# the reported bug — even if the OS drops the F key-up event because the HUD
# stole focus on the press, our internal state is already correct, so the
# next Ctrl+Alt+S press will match its chord exactly instead of arriving
# with a phantom F still attached.
#
# CRITICAL — Modifier-mangled letters:
# When Ctrl/Alt are held, Windows (and X11 with some layouts) does NOT send
# the letter key as a printable char. pynput delivers a KeyCode whose .char
# is either None or a control byte (e.g. Ctrl+F arrives as KeyCode(char='\x06')).
# Naively `char.lower()` then yields "\x06" — which never matches "f" — so
# the chord silently never fires. Two safety nets:
#   (a) Run every event through the Listener's `canonical()` helper. pynput
#       uses the OS keymap to recover the un-modified character.
#   (b) If `.char` is still missing or non-printable, fall back to the
#       virtual-key code (`.vk`) which is layout-stable on Windows.
# -----------------------------------------------------------------------------

_MODIFIER_IDS = frozenset({"ctrl", "alt", "shift", "cmd"})

# Virtual key code → canonical name. Used as fallback when KeyCode.char is
# None or a control byte (Ctrl/Alt held + letter on Windows).
_VK_TO_NAME = {}
# Letters A–Z → "a".."z"
for _i in range(0x41, 0x5B):
    _VK_TO_NAME[_i] = chr(_i).lower()
# Digits 0–9 → "0".."9"
for _i in range(0x30, 0x3A):
    _VK_TO_NAME[_i] = chr(_i)
# F1–F24
for _i in range(24):
    _VK_TO_NAME[0x70 + _i] = f"f{_i + 1}"
# A few common named keys
_VK_TO_NAME.update({
    0x20: "space", 0x09: "tab", 0x1B: "esc",
    0x0D: "enter", 0x08: "backspace",
})


def _parse_chord(spec: str) -> frozenset:
    """Parse '<ctrl>+<alt>+f' (pynput grammar) → frozenset of canonical ids."""
    parts = [p.strip() for p in spec.split("+") if p.strip()]
    out = set()
    for p in parts:
        name = p.strip("<>").lower()
        if name in ("ctrl", "control"):
            out.add("ctrl")
        elif name in ("alt", "alt_gr"):
            out.add("alt")
        elif name == "shift":
            out.add("shift")
        elif name in ("cmd", "super", "win"):
            out.add("cmd")
        else:
            out.add(name)  # f1..f12, single chars, space, tab, etc.
    return frozenset(out)


def _canon_key(key) -> str:
    """Convert a pynput Key/KeyCode → canonical id matching _parse_chord.

    Robust against the Ctrl/Alt+letter case where .char is None or a control
    byte: falls back to the virtual key code so 'F' under Ctrl+Alt still
    canonicalises to "f".
    """
    if not _KEYBOARD_AVAILABLE:
        return None
    Key = _pynput_kb.Key
    if isinstance(key, Key):
        n = key.name
        if n in ("ctrl_l", "ctrl_r"):
            return "ctrl"
        if n in ("alt_l", "alt_r", "alt_gr"):
            return "alt"
        if n in ("shift_l", "shift_r"):
            return "shift"
        if n in ("cmd_l", "cmd_r"):
            return "cmd"
        return n  # f1..f12, space, tab, esc, etc.

    # KeyCode (regular character)
    char = getattr(key, "char", None)
    # Accept only printable single chars. Ctrl+letter on Windows delivers a
    # control byte like '\x06' for Ctrl+F — that must NOT be returned, or
    # the chord {"ctrl","alt","f"} will never match {"ctrl","alt","\x06"}.
    if char and len(char) == 1 and char.isprintable():
        return char.lower()

    # Fallback: virtual key code. On Windows this is layout-stable, so VK
    # 0x46 always means 'F' regardless of which modifiers are held.
    vk = getattr(key, "vk", None)
    if vk is not None and vk in _VK_TO_NAME:
        return _VK_TO_NAME[vk]
    return None


class ChordWatcher:
    """Fires registered callbacks when exact key chords go down."""

    def __init__(self, hotkeys: dict):
        # hotkeys: {chord_string -> callback}
        self.chord_to_cb = {_parse_chord(k): cb for k, cb in hotkeys.items()}
        self.pressed = set()
        self.last_fire = {}
        self.cooldown = 0.4
        self.lock = threading.Lock()
        self._listener = None

    def _normalize(self, key):
        """Run the raw event through pynput's canonicaliser when possible.

        This recovers the un-modified character for Ctrl/Alt+letter combos so
        a press that arrives as KeyCode(char='\\x06', vk=70) becomes the
        proper 'f' KeyCode. If the listener isn't ready yet (first events
        before .start() returns) we hand back the raw key — _canon_key still
        has the vk fallback as a second safety net.
        """
        l = self._listener
        if l is None:
            return key
        try:
            return l.canonical(key)
        except Exception:
            return key

    def _on_press(self, key):
        key = self._normalize(key)
        c = _canon_key(key)
        if c is None:
            return
        with self.lock:
            if c in self.pressed:
                return  # autorepeat — ignore
            self.pressed.add(c)
            for chord, cb in self.chord_to_cb.items():
                # Rule 1: pressed set must EXACTLY equal the chord. No extra
                # keys allowed (so a stuck letter from a prior chord cannot
                # contaminate this one) and no missing keys (so a partial
                # press like Ctrl+Alt cannot fire a Ctrl+Alt+letter chord).
                if self.pressed == chord:
                    now = time.time()
                    if now - self.last_fire.get(chord, 0.0) > self.cooldown:
                        self.last_fire[chord] = now
                        # Proactively clear the chord's non-modifier keys
                        # from our internal state. If the OS later drops the
                        # key-up because focus was stolen by the HUD, our
                        # state is already correct — the letter is "released"
                        # as far as we're concerned. Modifiers stay tracked
                        # because users frequently hold Ctrl/Alt across
                        # successive shortcuts.
                        self.pressed -= (chord - _MODIFIER_IDS)
                        # Run on a thread so the listener loop never blocks
                        threading.Thread(target=cb, daemon=True).start()
                        return

    def _on_release(self, key):
        key = self._normalize(key)
        c = _canon_key(key)
        if c is None:
            return
        with self.lock:
            self.pressed.discard(c)

    def join(self):
        # Construct the listener first, assign it to self BEFORE starting it,
        # so even the very first _on_press has a listener to call .canonical()
        # against. (The previous `with ... as l:` form populated self._listener
        # only after the listener thread had already begun delivering events,
        # leaving an early-fire window where canonicalisation was skipped.)
        listener = _pynput_kb.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener = listener
        listener.start()
        try:
            listener.join()
        finally:
            self._listener = None


# --- PLATFORM-SAFE SUBPROCESS HELPERS ---
def _make_flags(detached=False):
    """
    Return the correct creationflags dict for subprocess.Popen.
    On non-Windows platforms creationflags must be 0 (or omitted entirely),
    otherwise Python raises ValueError.
    """
    if platform.system() != "Windows":
        return {}
    CREATE_NO_WINDOW = 0x08000000
    DETACHED_PROCESS = 0x00000008
    flags = CREATE_NO_WINDOW
    if detached:
        flags |= DETACHED_PROCESS
    return {"creationflags": flags}


def _get_python():
    """
    Return the best Python executable for spawning background processes.
    On Windows, prefer pythonw.exe so no console window appears.
    Falls back to the current interpreter on all other platforms.
    """
    exe = sys.executable
    if platform.system() == "Windows":
        base = os.path.dirname(exe)
        pythonw = os.path.join(base, "pythonw.exe")
        if os.path.exists(pythonw):
            return pythonw
    return exe


PYTHON_EXE = _get_python()


def _wait_for_hud_socket(timeout=6.0, poll=0.25):
    """
    Block until the HUD's TCP socket on :5555 is accepting connections
    (or the timeout expires).  Returns True when ready, False on timeout.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", 5555), timeout=0.3):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(poll)
    return False


class FlowSentinel:
    def __init__(self):
        # Per-hotkey debounce timestamps. They MUST be independent — a single
        # shared timer caused Ctrl+Alt+F immediately followed by the trigger
        # hotkey (or vice versa) to silently drop one of the two events.
        self._last_toggle_time = 0.0
        self._last_trigger_time = 0.0

        # toggle_hud needs a longer guard than the trigger because pynput on
        # Windows can re-detect Ctrl+Alt+<letter> as the user releases keys in
        # varied order — without a wide debounce the second fire instantly
        # closes what the first one just opened.
        self._toggle_debounce = 1.2
        self._trigger_debounce = 0.7

        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.hud_pid_file = os.path.join(self.base_dir, ".hud_pid")
        self.engine_pid_file = os.path.join(self.base_dir, ".engine_pid")
        self.hud_script = os.path.join(self.base_dir, "flow_hud.py")

    def _is_debouncing_toggle(self):
        now = time.time()
        if now - self._last_toggle_time < self._toggle_debounce:
            return True
        self._last_toggle_time = now
        return False

    def _is_debouncing_trigger(self):
        now = time.time()
        if now - self._last_trigger_time < self._trigger_debounce:
            return True
        self._last_trigger_time = now
        return False

    def _get_pid_from_file(self, path):
        """Read PID from file and verify the process is still alive."""
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    pid = int(f.read().strip())
                if psutil.pid_exists(pid):
                    return pid
            except Exception:
                pass
        return None

    def toggle_hud(self):
        """Toggle the HUD window on/off and sync the Ghost Pipeline."""
        log.info("⌨️  HUD hotkey received — toggle_hud() fired.")
        if self._is_debouncing_toggle():
            log.info(
                f"⏱️  Toggle debounce active (<{self._toggle_debounce}s since last). "
                "Ignoring re-fire from key bounce."
            )
            return

        active_pid = self._get_pid_from_file(self.hud_pid_file)

        if active_pid:
            log.info(f"🔪 HUD Active (PID {active_pid}). Executing Kill...")
            self._force_kill_hud()
        else:
            if os.path.exists(self.hud_pid_file):
                os.remove(self.hud_pid_file)

            log.info("🚀 HUD Dormant. Spawning Instance & Ghost Pipeline...")
            env = os.environ.copy()
            env["FLOW_UI_ALLOWED"] = "TRUE"

            # Run as a module (-m) so relative imports inside flow_hud.py work
            # correctly when DebugFlow is installed as a package.
            # Running the .py file directly would cause ImportError on
            # 'from . import log' and 'from .animation import FlowAnimator'.
            proc = subprocess.Popen(
                [PYTHON_EXE, "-m", "debugflow.flow_hud"],
                env=env,
                **_make_flags(detached=True),
            )
            with open(self.hud_pid_file, "w") as f:
                f.write(str(proc.pid))

            # Wait in background so the hotkey handler returns immediately —
            # the ghost pipeline fires as soon as the HUD socket is ready.
            threading.Thread(
                target=self._delayed_ghost_pipeline, daemon=True
            ).start()

    def _delayed_ghost_pipeline(self):
        """Wait for HUD socket, then fire the ghost scan (runs in a thread)."""
        ready = _wait_for_hud_socket(timeout=8.0)
        if ready:
            log.info("✅ HUD socket ready. Firing ghost pipeline...")
            self.ignite_ghost_pipeline()
        else:
            log.warning("⚠️ HUD socket did not become ready within 8s. Ghost scan skipped.")

    def ignite_ghost_pipeline(self):
        """Fire the Ghost Scout pass (structural scan) in a subprocess."""
        project_root = os.environ.get("FLOW_PROJECT_ROOT") or os.getcwd()
        target_script = os.environ.get("FLOW_CURRENT_SCRIPT")

        env = os.environ.copy()
        # Tell launch() to behave as a pure ghost scan (no live follow-through).
        env["FLOW_RUN_MODE"] = "ghost"

        engine_cmd = (
            "import sys; import os; "
            f"sys.path.insert(0, {repr(project_root)}); "
            f"if {repr(target_script)}: sys.path.insert(1, os.path.dirname({repr(target_script)})); "
            "from debugflow.flow_engine import FlowEngine; "
            "FlowEngine.ignite_from_service('GHOST_DRAW')"
        )

        try:
            subprocess.Popen(
                [PYTHON_EXE, "-c", engine_cmd],
                cwd=project_root,
                env=env,
                **_make_flags(detached=False),
            )
            log.info("📊 Ghost Pipeline: Architecture Snapshot triggered.")
        except Exception as e:
            log.error(f"Ghost Ignition Failed: {e}")

    def _force_kill_hud(self):
        """Surgically terminate the HUD process and all its children."""
        pid = self._get_pid_from_file(self.hud_pid_file)
        if pid:
            try:
                p = psutil.Process(pid)
                children = p.children(recursive=True)
                # Write poison-pill file so a cooperative shutdown fires first.
                die_path = os.path.join(self.base_dir, ".die")
                try:
                    open(die_path, "w").close()
                except Exception:
                    pass
                time.sleep(0.4)
                # Hard kill anything that's still alive.
                for child in children:
                    try:
                        child.kill()
                    except Exception:
                        pass
                try:
                    p.kill()
                except Exception:
                    pass
                log.info(f"Surgical Kill Success: {pid}")
            except Exception:
                pass
            finally:
                if os.path.exists(self.hud_pid_file):
                    os.remove(self.hud_pid_file)

    def log_save_event(self):
        """
        Triggered by the user-configurable trigger hotkey (default Ctrl+Alt+S).
        Runs the LIVE execution pass on the most-recently-modified script.
        """
        log.info("⌨️  Trigger hotkey received — log_save_event() fired.")

        if self._is_debouncing_trigger():
            log.info(
                f"⏱️  Trigger debounce active (<{self._trigger_debounce}s since last). Skipping."
            )
            return

        try:
            active_hud_pid = self._get_pid_from_file(self.hud_pid_file)
            if not (active_hud_pid and psutil.pid_exists(active_hud_pid)):
                log.warning(
                    "🚫 Ctrl+Alt+S ignored: HUD is not running. "
                    "Press Ctrl+Alt+F first to open the HUD."
                )
                return
            log.info(f"🤝 HUD alive (PID {active_hud_pid}). Proceeding with LIVE engine ignite.")

            old_engine_pid = self._get_pid_from_file(self.engine_pid_file)
            if old_engine_pid and psutil.pid_exists(old_engine_pid):
                try:
                    proc = psutil.Process(old_engine_pid)
                    proc.terminate()
                    try:
                        proc.wait(timeout=0.15)
                    except psutil.TimeoutExpired:
                        proc.kill()
                    log.info(f"🔪 Terminated stale engine (PID {old_engine_pid})")
                except Exception as e:
                    log.warning(f"Could not terminate stale engine PID {old_engine_pid}: {e}")

            sync_id = str(uuid.uuid4())[:8]
            project_root = os.environ.get("FLOW_PROJECT_ROOT") or os.getcwd()
            log.info(
                f"📡 Launching LIVE engine session {sync_id} from project root: {project_root}"
            )

            env = os.environ.copy()
            # Tell launch() to skip ghost and run live directly.
            env["FLOW_RUN_MODE"] = "live"

            engine_cmd = (
                "import sys; "
                f"sys.path.insert(0, {repr(os.path.dirname(self.base_dir))}); "
                "from debugflow.flow_engine import FlowEngine; "
                f"FlowEngine.ignite_from_service({repr(sync_id)})"
            )

            new_engine = subprocess.Popen(
                [PYTHON_EXE, "-c", engine_cmd],
                cwd=project_root,
                env=env,
                **_make_flags(detached=False),
            )

            with open(self.engine_pid_file, "w") as f:
                f.write(str(new_engine.pid))

            log.info(f"🚀 Engine spawned (PID {new_engine.pid}).")

        except Exception as e:
            log.error(f"Failed to sync save event: {e}")

    def start_listening(self):
        """Bind hotkeys and block until the keyboard listener exits."""
        if not _KEYBOARD_AVAILABLE:
            log.error(
                "pynput unavailable — sentinel is running without hotkeys. "
                "On Linux ensure a display server (X11/Wayland) is running."
            )
            while True:
                time.sleep(10)
            return

        hud_hotkey = os.environ.get("FLOW_HUD_HOTKEY", "<ctrl>+<alt>+f").strip()
        trigger_hotkey = os.environ.get("FLOW_TRIGGER_HOTKEY", "<ctrl>+<alt>+s").strip()
        log.info(f"⌨️  Binding hotkeys: HUD='{hud_hotkey}', TRIGGER='{trigger_hotkey}'")

        try:
            hotkeys = {
                hud_hotkey: self.toggle_hud,
                trigger_hotkey: self.log_save_event,
            }
            watcher = ChordWatcher(hotkeys)
            watcher.join()
        except Exception as e:
            log.error(f"Hotkey listener crashed: {e}\n{traceback.format_exc()}")


_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_SENTINEL_PID_FILE = os.path.join(_BASE_DIR, ".sentinel_pid")


def _pretty(h: str) -> str:
    """Format a pynput hotkey spec like '<ctrl>+<alt>+s' into 'Ctrl + Alt + S'."""
    return (
        h.replace("<", "").replace(">", "")
         .replace("ctrl", "Ctrl").replace("alt", "Alt")
         .replace("shift", "Shift").replace("cmd", "Cmd")
         .replace("+", " + ").upper()
         .replace("CTRL", "Ctrl").replace("ALT", "Alt")
         .replace("SHIFT", "Shift").replace("CMD", "Cmd")
    )


def _cmd_activate():
    """Spawn the sentinel as a detached background process and print the banner."""
    if os.path.exists(_SENTINEL_PID_FILE):
        try:
            with open(_SENTINEL_PID_FILE) as f:
                pid = int(f.read().strip())
            if psutil.pid_exists(pid):
                print(
                    f"\n  DebugFlow sentinel is already running (PID {pid}).\n"
                    "  Use `debugflow deactivate` to stop it.\n"
                )
                return
        except Exception:
            pass
        try:
            os.remove(_SENTINEL_PID_FILE)
        except Exception:
            pass

    env = os.environ.copy()
    env["FLOW_SENTINEL_WORKER"] = "1"
    env["FLOW_PROJECT_ROOT"] = env.get("FLOW_PROJECT_ROOT") or os.getcwd()

    if platform.system() == "Windows":
        proc = subprocess.Popen(
            [PYTHON_EXE, "-m", "debugflow.flow_service"],
            env=env,
            **_make_flags(detached=True),
            close_fds=True,
        )
    else:
        proc = subprocess.Popen(
            [PYTHON_EXE, "-m", "debugflow.flow_service"],
            env=env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )

    # Brief pause so the worker can write its PID file and bind hotkeys
    time.sleep(0.3)

    hud_hotkey = env.get("FLOW_HUD_HOTKEY", "<ctrl>+<alt>+f")
    trigger_hotkey = env.get("FLOW_TRIGGER_HOTKEY", "<ctrl>+<alt>+s")

    print(
        "\n  ──────────────────────────────────────\n"
        "  DebugFlow hotkeys active:\n"
        f"        Toggle HUD     : {_pretty(hud_hotkey)}\n"
        f"        Run / Trigger  : {_pretty(trigger_hotkey)}\n"
        "  ──────────────────────────────────────\n"
        "  Logs  :  debugflow-logs on / off\n"
        "  Stop  :  debugflow deactivate\n"
        "  ──────────────────────────────────────\n"
    )


def _cmd_deactivate():
    """Stop the sentinel and the HUD."""
    killed = False

    if os.path.exists(_SENTINEL_PID_FILE):
        try:
            with open(_SENTINEL_PID_FILE) as f:
                pid = int(f.read().strip())
            if psutil.pid_exists(pid):
                psutil.Process(pid).terminate()
                print(f"\n  Sentinel stopped  (PID {pid}).")
                killed = True
        except Exception:
            pass
        try:
            os.remove(_SENTINEL_PID_FILE)
        except Exception:
            pass

    hud_pid_file = os.path.join(_BASE_DIR, ".hud_pid")
    if os.path.exists(hud_pid_file):
        try:
            with open(hud_pid_file) as f:
                hud_pid = int(f.read().strip())
            if psutil.pid_exists(hud_pid):
                psutil.Process(hud_pid).terminate()
                print(f"  HUD closed        (PID {hud_pid}).")
        except Exception:
            pass
        try:
            os.remove(hud_pid_file)
        except Exception:
            pass

    if not killed:
        print("\n  Sentinel is not running.")
    print()


def _cmd_status():
    """Print whether the sentinel is running."""
    running = False
    if os.path.exists(_SENTINEL_PID_FILE):
        try:
            with open(_SENTINEL_PID_FILE) as f:
                pid = int(f.read().strip())
            if psutil.pid_exists(pid):
                print(f"\n  Sentinel : RUNNING  (PID {pid})\n")
                running = True
        except Exception:
            pass
    if not running:
        print("\n  Sentinel : STOPPED\n")


def _print_usage():
    print(
        "\n  Usage: debugflow <command>\n"
        "\n  Commands:\n"
        "    activate     Start the hotkey sentinel in the background\n"
        "    deactivate   Stop the sentinel and close the HUD\n"
        "    status       Show whether the sentinel is running\n"
        "\n  Log commands:\n"
        "    debugflow-logs on    Enable file logging\n"
        "    debugflow-logs off   Disable file logging\n"
        "    debugflow-logs       Toggle logging\n"
        "\n  If the HUD or sentinel freezes:\n"
        "    debugflow deactivate          — clean stop\n"
        "    kill <PID>                    — Unix: use PID from `debugflow status`\n"
        "    taskkill /PID <PID> /F        — Windows equivalent\n"
    )


def main():
    import signal

    # --- BACKGROUND WORKER PATH ---
    # When re-spawned by _cmd_activate, we are the daemon.  Register SIGTERM
    # for a clean shutdown and drop straight into the hotkey loop.
    if os.environ.get("FLOW_SENTINEL_WORKER") == "1":
        def _shutdown(sig, frame):
            sys.exit(0)
        try:
            signal.signal(signal.SIGTERM, _shutdown)
        except Exception:
            pass
        try:
            with open(_SENTINEL_PID_FILE, "w") as f:
                f.write(str(os.getpid()))
        except Exception:
            pass
        FlowSentinel().start_listening()
        return

    # --- CLI DISPATCHER ---
    cmd = (sys.argv[1].lower().strip() if len(sys.argv) > 1 else "").strip()
    if cmd == "activate":
        _cmd_activate()
    elif cmd == "deactivate":
        _cmd_deactivate()
    elif cmd == "status":
        _cmd_status()
    else:
        _print_usage()


if __name__ == "__main__":
    main()
