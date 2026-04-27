import sys
import os
import time
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


class FlowSentinel:
    def __init__(self):
        self.last_trigger_time = 0
        self.debounce_duration = 0.7
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.hud_pid_file = os.path.join(self.base_dir, ".hud_pid")
        self.engine_pid_file = os.path.join(self.base_dir, ".engine_pid")
        self.hud_script = os.path.join(self.base_dir, "flow_hud.py")

    def _is_debouncing(self):
        """Strict mechanical debounce."""
        current_time = time.time()
        if current_time - self.last_trigger_time < self.debounce_duration:
            return True
        self.last_trigger_time = current_time
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
        log.info("⌨️  Ctrl+Alt+F received — toggle_hud() fired.")
        if self._is_debouncing():
            log.info("⏱️  Debounce active, skipping.")
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

            time.sleep(0.5)
            self.ignite_ghost_pipeline()

    def ignite_ghost_pipeline(self):
        """Fire the Ghost Scout pass so the HUD shows the project architecture."""
        try:
            sync_id = "GHOST_DRAW"
            # Use the installed package path so this works both in dev and as a package
            engine_cmd = (
                "import sys; "
                f"sys.path.insert(0, {repr(os.path.dirname(self.base_dir))}); "
                "from debugflow.flow_engine import FlowEngine; "
                f"FlowEngine.ignite_from_service({repr(sync_id)})"
            )

            # No cwd override — inherit the sentinel's working directory,
            # which is the user's project folder (set when `flow activate` ran).
            # Using cwd=self.base_dir was pointing at the package's own directory,
            # causing ignite_from_service to find animation.py / flow_bridge.py
            # instead of the user's scripts.
            subprocess.Popen(
                [PYTHON_EXE, "-c", engine_cmd],
                **_make_flags(detached=False),
            )
            log.info("📊 Ghost Pipeline: Architecture Snapshot Sent.")
        except Exception as e:
            log.error(f"Ghost Ignition Failed: {e}")

    def _force_kill_hud(self):
        """Surgically terminate the HUD process and all its children."""
        pid = self._get_pid_from_file(self.hud_pid_file)
        if pid:
            try:
                p = psutil.Process(pid)
                for child in p.children(recursive=True):
                    child.kill()
                p.kill()
                log.info(f"Surgical Kill Success: {pid}")
            except Exception:
                pass
            finally:
                if os.path.exists(self.hud_pid_file):
                    os.remove(self.hud_pid_file)

    def log_save_event(self):
        """
        Triggered by Ctrl+S.
        Ignites the engine only when the HUD is physically alive in the OS.
        """
        if self._is_debouncing():
            return

        try:
            active_hud_pid = self._get_pid_from_file(self.hud_pid_file)
            if not (active_hud_pid and psutil.pid_exists(active_hud_pid)):
                return

            old_engine_pid = self._get_pid_from_file(self.engine_pid_file)
            if old_engine_pid and psutil.pid_exists(old_engine_pid):
                try:
                    psutil.Process(old_engine_pid).terminate()
                    log.info(f"🔪 Terminated stale engine (PID {old_engine_pid})")
                except Exception:
                    pass

            sync_id = str(uuid.uuid4())[:8]
            log.info(f"📡 HUD Linked. Launching Session: {sync_id}")

            # Always reference the installed package so this works outside the src/ tree
            engine_cmd = (
                "import sys; "
                f"sys.path.insert(0, {repr(os.path.dirname(self.base_dir))}); "
                "from debugflow.flow_engine import FlowEngine; "
                f"FlowEngine.ignite_from_service({repr(sync_id)})"
            )

            # Same fix as ghost pipeline — no cwd override so the engine
            # scans the user's project directory, not the package source.
            new_engine = subprocess.Popen(
                [PYTHON_EXE, "-c", engine_cmd],
                **_make_flags(detached=False),
            )

            with open(self.engine_pid_file, "w") as f:
                f.write(str(new_engine.pid))

        except Exception as e:
            log.error(f"Failed to sync save event: {e}")

    def start_listening(self):
        """
        Entry point for the background daemon.
        Binds Ctrl+Alt+F and Ctrl+S using pynput GlobalHotKeys, which works
        cross-platform without needing root on Linux desktop environments.
        """
        if not _KEYBOARD_AVAILABLE:
            print(
                "  ⚠️  [HOTKEYS UNAVAILABLE]: pynput could not be loaded.\n"
                "  On Linux ensure a display server (X11/Wayland) is running.\n"
                "  Install manually: pip install pynput"
            )
            log.error("pynput unavailable. Sentinel will idle without hotkeys.")
            # Keep the process alive so the PID file stays valid — do not exit.
            while True:
                time.sleep(10)
            return

        log.info("⌨️  Binding hotkeys via pynput...")
        try:
            hotkeys = {
                "<ctrl>+<alt>+f": self.toggle_hud,
                "<ctrl>+s": self.log_save_event,
            }
            with _pynput_kb.GlobalHotKeys(hotkeys) as hk:
                log.info("System Hooks Active. Sentinel in Standby.")
                hk.join()
        except Exception as e:
            log.error(f"Sentinel Loop Fatal Error: {e}")
            print(f"  ⚠️  [SENTINEL ERROR]: {e}")


def is_service_running():
    """Check if a FlowSentinel daemon is already running in another process."""
    try:
        current_pid = os.getpid()
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            cmdline = proc.info.get("cmdline")
            if not cmdline:
                continue
            cmd_str = " ".join(cmdline)
            if "flow_service" in cmd_str and proc.info["pid"] != current_pid:
                return proc
    except Exception:
        pass
    return None


def activate():
    """
    CLI entry point (`flow activate`).
    Toggles the NeuralFlow service on/off.
    """
    kill_signal = os.path.join(os.path.dirname(__file__), ".die")
    if os.path.exists(kill_signal):
        os.remove(kill_signal)

    existing_proc = is_service_running()

    # --- TOGGLE OFF ---
    if existing_proc:
        if platform.system() != "Windows" or "pythonw.exe" not in sys.executable.lower():
            sentinel = FlowSentinel()
            sentinel._force_kill_hud()
            try:
                existing_proc.kill()
                time.sleep(0.2)
            except Exception:
                pass

            print("\n  [!] SYNAPSE DISCONNECTED")
            print("  ✖ NEURALFLOW: DEACTIVATED\n")
            sys.exit(0)

    # --- START SERVICE ---
    subprocess.Popen(
        [PYTHON_EXE, "-m", "debugflow.flow_service"],
        close_fds=True,
        **_make_flags(detached=True),
    )

    print("\n" + "═" * 50)
    print("  ✔  NEURALFLOW: ENGINE ACTIVATED")
    print("═" * 50)
    print("  [NODE PROTOCOLS]")
    print("  🟡 YELLOW : Processing (Active Thread)")
    print("  🟢 GREEN  : Success    (Data Synapse)")
    print("  🔴 RED    : Nuke       (Point of Failure)")
    print("─" * 50)
    print("  [EXECUTION MODES]")
    print("  👻 GHOST  : Non-destructive Logic Mapping")
    print("  ⏳ LIVE   : Real-time Production Trace")
    print("─" * 50)
    print("  [SYSTEM CONTROLS]")
    print("  • CTRL + ALT + F : Toggle HUD & Ghost Sync")
    print("  • CTRL + S       : Auto-Trace (If HUD is open)")
    print("  • flow activate  : Deactivate NeuralFlow")
    print("─" * 50)
    print("  [LOGGING]")
    print("  • flow-logs on   : Enable debug log  → ./logs/debugflow.log")
    print("  • flow-logs off  : Disable logging")
    print("  • flow-logs status : Check current state")
    print("═" * 50)
    print("  Sentinel is monitoring the nervous system...\n")


def _print_usage():
    print("\n" + "═" * 50)
    print("  flow — NeuralFlow Logic Engine CLI")
    print("═" * 50)
    print("  Usage:")
    print("    flow activate            Toggle the NeuralFlow sentinel on/off")
    print("    flow status              Show whether the sentinel is running")
    print("    flow help                Show this message")
    print()
    print("  Logging (separate command):")
    print("    flow-logs on | off | status")
    print("═" * 50 + "\n")


def _print_status():
    proc = is_service_running()
    if proc:
        print(f"\n  ● NEURALFLOW: RUNNING  (PID {proc.pid})\n")
    else:
        print("\n  ○ NEURALFLOW: STOPPED\n")


def main():
    """
    Top-level CLI dispatcher for the `flow` command.

    Only the explicit `activate` subcommand toggles the sentinel.
    Bare `flow`, `flow help`, or any unknown subcommand (e.g. `flow loggies`,
    `flow xyz`) prints usage instead of silently activating, which used to
    happen when this entry point was wired straight to activate().
    """
    args = sys.argv[1:]

    if not args:
        _print_usage()
        return

    cmd = args[0].lower()

    if cmd == "activate":
        activate()
    elif cmd in ("status", "state"):
        _print_status()
    elif cmd in ("help", "-h", "--help"):
        _print_usage()
    else:
        print(f"\n  ⚠️  Unknown command: 'flow {cmd}'")
        _print_usage()


if __name__ == "__main__":
    try:
        sentinel = FlowSentinel()
        sentinel.start_listening()
    except Exception as e:
        log.error(f"Background Startup Failed: {e}")
