"""
LoggerSystem — persistent, flag-file-controlled logging for DebugFlow.

Usage:
    flow-logs on     → enables file logging  (all modules)
    flow-logs off    → disables file logging (complete silence)

The ON/OFF state is stored in ~/.debugflow/.debug_on so it survives
restarts, new terminals, and reinstalls.

Log output goes to ~/.debugflow/debugflow.log
"""
import logging
import sys
from pathlib import Path


# --- PATHS -----------------------------------------------------------
_FLOW_DIR = Path.home() / ".debugflow"
_FLAG_FILE = _FLOW_DIR / ".debug_on"
_LOG_FILE  = _FLOW_DIR / "debugflow.log"
# ---------------------------------------------------------------------


def _is_debug_on() -> bool:
    """Return True when the user has toggled logging ON via `flow-logs on`."""
    return _FLAG_FILE.exists()


class LoggerSystem:
    _BASE_NAME = "debugflow"

    @staticmethod
    def setup() -> logging.Logger:
        """
        Configure the root 'debugflow' logger.

        ON  → writes everything (DEBUG+) to ~/.debugflow/debugflow.log.
               No output to the terminal so the user's console stays clean.
        OFF → attaches a NullHandler; absolute silence, zero file I/O.
        """
        logger = logging.getLogger(LoggerSystem._BASE_NAME)

        # Prevent double-configuration if setup() is called more than once
        if logger.handlers:
            return logger

        logger.propagate = False  # Never bleed into the root logger / terminal

        if not _is_debug_on():
            logger.addHandler(logging.NullHandler())
            return logger

        # --- ON: file-only handler ---
        _FLOW_DIR.mkdir(parents=True, exist_ok=True)

        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()

        fh = logging.FileHandler(_LOG_FILE, mode="a", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)-8s] [%(name)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(fh)

        return logger


# Module-level singleton — imported by every other module as `from . import log`
log = LoggerSystem.setup()


# --- CLI HELPERS -----------------------------------------------------

def _print_status():
    state = "ON " if _is_debug_on() else "OFF"
    print(f"\n  DebugFlow logging is currently: {state}")
    if _is_debug_on():
        print(f"  Log file → {_LOG_FILE}\n")
    else:
        print()


def logs_on():
    """Entry point for `flow-logs on`."""
    _FLOW_DIR.mkdir(parents=True, exist_ok=True)
    _FLAG_FILE.touch()

    print("\n" + "─" * 45)
    print("  ✔  NEURALFLOW LOGGING: ENABLED")
    print(f"  Log file → {_LOG_FILE}")
    print("  Restart `flow activate` to apply to the sentinel.")
    print("─" * 45 + "\n")


def logs_off():
    """Entry point for `flow-logs off`."""
    if _FLAG_FILE.exists():
        _FLAG_FILE.unlink()

    print("\n" + "─" * 45)
    print("  ✖  NEURALFLOW LOGGING: DISABLED")
    print("  No data will be written to disk.")
    print("  Restart `flow activate` to apply to the sentinel.")
    print("─" * 45 + "\n")


def toggle_logs_cli():
    """
    Entry point for the `flow-logs` command.
    Usage: flow-logs on | flow-logs off | flow-logs status
    """
    args = sys.argv[1:]

    if not args or args[0] not in ("on", "off", "status"):
        print("\n  Usage: flow-logs on | flow-logs off | flow-logs status\n")
        return

    cmd = args[0]
    if cmd == "on":
        logs_on()
    elif cmd == "off":
        logs_off()
    else:
        _print_status()
