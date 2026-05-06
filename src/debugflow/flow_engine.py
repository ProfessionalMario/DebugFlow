"""
File summary: Summary unavailable due to analysis error.
"""

import builtins
import sys
import os
import inspect
import time
import socket
import traceback
import importlib.util
import subprocess
import platform
from . import log
from .flow_bridge import Flow



# --- GHOST FLOW LOOP GUARDS ---
# Bound how much work a single ghost pass can do so a user's `while True:` /
# blocking input call / runaway recursion never freezes the HUD.
#
#   MAX_GHOST_CALLS    — hard cap on trace_calls 'call' events per pass.
#   MAX_GHOST_SECONDS  — wall-clock cap on the entire ghost pass.
#   MAX_GHOST_INPUTS   — how many times the patched input() may return
#                        "GHOST_DATA" before it raises a trap and bails out.
#                        This is what tames `while True: x = input()` loops.
MAX_GHOST_CALLS = 50
MAX_GHOST_SECONDS = 3.0
MAX_GHOST_INPUTS = 5

_ghost_call_count = [0]   # Mutable list → mutate without `global`
_ghost_start_time = [0.0]
_ghost_input_count = [0]
_ghost_aborted = [False]

# --- PER-CALL TIMING ---
# Maps id(frame) → start timestamp so we can compute total time spent in each
# function call. Frame ids are unique for the lifetime of a single invocation,
# which means recursion / re-entrant calls each get their own entry.
_call_start_ts = {}


def _stop_ghost_trace(reason: str):
    """
    Cleanly stop the ghost trace without firing a 'nuke' node.

    The currently-running function stays in its existing 'processing' state
    (yellow) on the HUD because no success/exception event is dispatched —
    which matches the user's expectation: the function really *is* still in
    progress, we just stopped *watching* it.
    """
    if _ghost_aborted[0]:
        return
    _ghost_aborted[0] = True
    log.warning(f"⚠️ Ghost trace stopped: {reason}")
    sys.settrace(None)


# --- PLATFORM-SAFE SUBPROCESS HELPERS ---
def _popen_detached(cmd, cwd=None, env=None):
    """
    Spawn a background process in a platform-safe way.
    On Windows: no console window + detached.
    On other platforms: just Popen normally.
    """
    kwargs = {"cwd": cwd, "env": env}
    if platform.system() == "Windows":
        CREATE_NO_WINDOW = 0x08000000
        DETACHED_PROCESS = 0x00000008
        kwargs["creationflags"] = CREATE_NO_WINDOW | DETACHED_PROCESS
    return subprocess.Popen(cmd, **kwargs)


class FlowEngine:
    @staticmethod
    def ignite_from_service(sync_id):
        """
        Engine subprocess entry point. Discovers the user's most recently
        modified .py file in FLOW_PROJECT_ROOT (or cwd) and runs it as
        __main__ so any `if __name__ == "__main__": launch(...)` block fires.
        """
        try:
            # Use FLOW_PROJECT_ROOT first so file discovery is deterministic
            # even when the engine is spawned by a detached pythonw.exe whose
            # cwd may have drifted to C:\Users\<name>.
            project_root = os.environ.get("FLOW_PROJECT_ROOT") or os.getcwd()
            log.info(f"🔍 Engine scanning project root: {project_root}  (sync_id={sync_id})")

            ignore = {
                "flow_service.py",
                "flow_engine.py",
                "logger_system.py",
                "flow_hud.py",
                "flow_bridge.py",
                "animation.py",
            }
            try:
                all_py = [
                    f for f in os.listdir(project_root)
                    if f.endswith(".py") and f not in ignore
                ]
            except FileNotFoundError:
                log.error(f"❌ Project root does not exist: {project_root}")
                return

            if not all_py:
                log.error(
                    f"❌ No user .py scripts in {project_root}. "
                    "Make sure `flow activate` was run from the right folder."
                )
                return

            # Pick the most recently modified file — that's almost always the
            # one the user just saved with Ctrl+Alt+S.
            target_script = max(
                all_py, key=lambda f: os.path.getmtime(os.path.join(project_root, f))
            )
            target_abs = os.path.join(project_root, target_script)
            log.info(
                f"🔥 Auto-booting most-recent script: {target_script}  "
                f"(of {len(all_py)} candidate{'s' if len(all_py) != 1 else ''})"
            )

            os.environ["FLOW_SYNC_ID"] = sync_id

            spec = importlib.util.spec_from_file_location("__main__", target_abs)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            log.info(f"✅ Engine finished executing {target_script}.")
        except Exception as e:
            log.critical(f"💥 Engine Discovery Crash: {e}", exc_info=True)

    @staticmethod
    def run_target(script_path, target_func, sync_id, params=None):
        abs_path = os.path.normpath(os.path.abspath(script_path))
        os.environ["FLOW_CURRENT_SCRIPT"] = abs_path

        # --- HUD PERSISTENCE CHECK ---
        try:
            with socket.create_connection(("127.0.0.1", 5555), timeout=0.1):
                log.info("🤝 HUD Link Verified.")
        except Exception:
            log.warning("⚠️ HUD missing. Spawning UI...")
            env = os.environ.copy()
            env["FLOW_UI_ALLOWED"] = "TRUE"
            # Use -m so relative imports inside flow_hud.py resolve correctly
            _popen_detached([sys.executable, "-m", "debugflow.flow_hud"], env=env)
            time.sleep(1.2)

        try:
            Flow.init(sync_id)

            is_rt = os.environ.get("FLOW_REAL_TIME") == "TRUE"

            Flow.pulse(
                "SYSCALL: REFRESH",
                params={"mode": "LIVE", "target": target_func.__name__, "real_time": is_rt},
            )

            log.info(f"🚀 LIVE TRACE STARTING: {target_func.__name__} | Sync: {is_rt}")

            sys.settrace(trace_calls)
            try:
                if params and inspect.signature(target_func).parameters:
                    target_func(**params)
                else:
                    target_func()
            finally:
                sys.settrace(None)

            log.info("✅ LIVE Execution Finished.")
        except Exception as e:
            log.error(f"❌ Execution Error: {e}")
            Flow.pulse("ENGINE: FATAL", returns=str(e), node_type="nuke")


def trace_calls(frame, event, arg):
    """
    sys.settrace hook — captures every function call and return in the target script
    and forwards the data to the HUD via Flow.pulse.

    For 'call' events  : sends the function's live local variables as params so the
                         type-gate in Flow.pulse can validate them against annotations.
    For 'return' events: sends the actual return value for return-type validation.
    For 'exception'    : marks the node as a nuke and disables further tracing.
    """
    filename = frame.f_code.co_filename
    func_name = frame.f_code.co_name

    if "logging" in filename or "flow_engine" in filename or "flow_bridge" in filename:
        return trace_calls

    if os.environ.get("FLOW_BLAST_CRITICAL") == "TRUE":
        return None

    target_file = os.environ.get("FLOW_CURRENT_SCRIPT")
    current_file = os.path.normpath(os.path.abspath(filename))

    if target_file and current_file == target_file:
        if func_name in ["<module>", "launch"]:
            return trace_calls

        # Resolve the actual function object so the type-gate can inspect its signature
        fn_obj = frame.f_globals.get(func_name) or (
            frame.f_back.f_locals.get(func_name) if frame.f_back else None
        )

        is_rt = os.environ.get("FLOW_REAL_TIME") == "TRUE"
        is_ghost = os.environ.get("FLOW_MODE") == "SIMULATION"

        # --- SOURCE METADATA (for click-to-source + hover module label) ---
        # File + first line of the function definition so the HUD can open
        # the editor at the right place. Module name comes from the frame's
        # __name__ so user code in `__main__` shows as such, with a basename
        # fallback for safety.
        src_file = current_file
        src_line = frame.f_code.co_firstlineno
        module_name = (
            frame.f_globals.get("__name__")
            or os.path.splitext(os.path.basename(current_file))[0]
        )

        if event == "call":
            # --- GHOST LOOP GUARDS ---
            # During a ghost pass, bound how much we trace so the user's
            # infinite loops, deep recursion, or blocking input() calls never
            # freeze the HUD. We *do not* fire a 'nuke' node — the function is
            # genuinely still in progress, so we leave its existing 'processing'
            # (yellow) state alone and just stop watching.
            if is_ghost:
                _ghost_call_count[0] += 1

                if _ghost_call_count[0] > MAX_GHOST_CALLS:
                    _stop_ghost_trace(
                        f"{MAX_GHOST_CALLS} calls reached (likely loop)."
                    )
                    return None

                if (
                    _ghost_start_time[0]
                    and (time.time() - _ghost_start_time[0]) > MAX_GHOST_SECONDS
                ):
                    _stop_ghost_trace(
                        f"{MAX_GHOST_SECONDS}s wall-clock budget exceeded."
                    )
                    return None

            # Stamp call start so the matching return/exception can compute
            # the total time the function took. Keyed by frame id so recursive
            # / re-entrant invocations each get their own timer.
            _call_start_ts[id(frame)] = time.perf_counter()

            # Capture the live local variables at call time.
            live_params = {k: _safe_serialize(v) for k, v in frame.f_locals.items()}
            Flow.pulse(
                fn_obj or func_name,
                params=live_params,
                node_type="processing",
                file=src_file,
                line=src_line,
                module=module_name,
            )
            if not is_rt and not is_ghost:
                # Cinematic delay only in live mode — ghost runs instantly
                time.sleep(1.0)

        elif event == "exception":
            os.environ["FLOW_BLAST_CRITICAL"] = "TRUE"
            duration_ms = _pop_duration_ms(frame)
            Flow.pulse(
                fn_obj or func_name,
                node_type="nuke",
                file=src_file,
                line=src_line,
                module=module_name,
                duration_ms=duration_ms,
            )
            return None

        elif event == "return":
            # arg is the actual return value from Python's trace protocol
            duration_ms = _pop_duration_ms(frame)
            Flow.pulse(
                fn_obj or func_name,
                returns=arg,
                node_type="success",
                file=src_file,
                line=src_line,
                module=module_name,
                duration_ms=duration_ms,
            )
            if not is_rt and not is_ghost:
                # Cinematic delay only in live mode
                time.sleep(0.5)

    return trace_calls


def _pop_duration_ms(frame):
    """Compute and clear the elapsed time (ms) for a frame's matching call."""
    start = _call_start_ts.pop(id(frame), None)
    if start is None:
        return None
    return round((time.perf_counter() - start) * 1000.0, 2)


def secure_gate(mode="LIVE"):
    """
    Handles environment muzzling and safety shims.

    Args:
        mode: "SIMULATION" for ghost pass, "LIVE" for real execution.

    Returns:
        None
    """
    if not os.environ.get("FLOW_DEBUG_MODE") == "TRUE":
        import logging
        for logger_name in [None, "FlowEngine", "FlowService"]:
            l = logging.getLogger(logger_name)
            for handler in l.handlers:
                if isinstance(handler, logging.StreamHandler):
                    handler.setLevel(logging.ERROR)

    if mode == "SIMULATION":
        # Bounded input shim: returns mock data for the first MAX_GHOST_INPUTS
        # calls, then raises a trap so a `while True: x = input()` loop in the
        # user's code can't pin the ghost pass forever. The trap is caught in
        # launch() the same way Ghost Exit Trap is.
        def _ghost_input(prompt=""):
            _ghost_input_count[0] += 1
            if _ghost_input_count[0] > MAX_GHOST_INPUTS:
                raise RuntimeError("Ghost Input Trap")
            return "GHOST_DATA"

        builtins.input = _ghost_input
        sys.exit = lambda code=None: (_ for _ in ()).throw(
            RuntimeError("Ghost Exit Trap")
        )
    else:
        # LIVE mode — leave input/exit untouched so real CLI flows work.
        pass


def generate_ball(annotation):
    """
    Produce a plausible mock value for a given type annotation.

    Args:
        annotation: A type object (int, str, etc.) or inspect.Parameter.empty
                    if the parameter has no annotation.

    Returns:
        A mock value matching the annotation, or None when no mapping exists.
    """
    if annotation is inspect.Parameter.empty:
        return None
    mapping = {
        int: 1,
        str: "mock_val",
        float: 1.0,
        bool: True,
        list: [],
        dict: {},
        tuple: (),
    }
    if annotation in mapping:
        return mapping[annotation]
    # Light ML / scientific-stack support.  Detect by qualified name so we
    # never add hard imports of torch / numpy / pandas — they're imported
    # lazily, and only when the user has actually annotated a parameter
    # with that type (which means they must already have the library
    # installed).  Smallest possible mock per type to keep ghost-pass
    # cost negligible.
    mod = getattr(annotation, "__module__", "") or ""
    name = getattr(annotation, "__name__", "") or ""
    try:
        if mod.startswith("torch") and name == "Tensor":
            import torch
            return torch.zeros(1)
        if mod.startswith("numpy") and name == "ndarray":
            import numpy as np
            return np.zeros(1)
        if mod.startswith("pandas"):
            if name == "DataFrame":
                import pandas as pd
                return pd.DataFrame()
            if name == "Series":
                import pandas as pd
                return pd.Series(dtype=float)
    except Exception:
        return None
    return None



def _safe_serialize(obj, depth=0):
    """
    Converts complex objects (Tensors, Arrays, DataFrames) into 
    metadata strings that an LLM can actually reason about.
    """
    if depth > 2: return "..." # Prevent infinite recursion
    
    # --- Torch Tensors ---
    if hasattr(obj, "__module__") and "torch" in obj.__module__:
        if hasattr(obj, "shape"):
            dtype = getattr(obj, "dtype", "unknown")
            return f"torch.Tensor(shape={list(obj.shape)}, dtype={dtype})"
            
    # --- Numpy Arrays ---
    try:
        import numpy as _np
        if isinstance(obj, _np.ndarray):
            return f"np.ndarray(shape={obj.shape}, dtype={obj.dtype})"
    except ImportError:
        pass
        
    # --- Standard Collections ---
    if isinstance(obj, dict):
        return {k: _safe_serialize(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(v, depth + 1) for v in obj[:5]] # Cap preview
        
    # --- Fallback to String ---
    try:
        return str(obj)
    except:
        return "<Unserializable Object>"



def launch(func_name, Ghost=True, Real_Time=True, _func_ref=None, _params=None):
    """
    The master airlock.

    FLOW_RUN_MODE env var (set by the service) overrides the Ghost parameter:
        "ghost" → force ghost-only scan (HUD auto-scan on open)
        "live"  → force live execution (Ctrl+Alt+S trigger)
        absent  → respect the Ghost argument (direct script invocation)

    Args:
        func_name:  Name of the function to trace (string, looked up in caller's globals).
        Ghost:      If True, run the Ghost Scout pass with mock params first.
        Real_Time:  If True, cinematic delays are skipped; execution runs at CPU speed.
        _func_ref:  Internal — pre-resolved function object (used on recursive handover).
        _params:    Internal — mock params carried from Ghost → Live handover.

    Returns:
        None
    """
    # --- SERVICE MODE OVERRIDE ---
    # When invoked by the service (via ignite_from_service), FLOW_RUN_MODE
    # dictates whether we run a ghost scan or a live execution regardless of
    # what the user's script says in the Ghost= argument.
    service_mode = os.environ.get("FLOW_RUN_MODE", "")
    if service_mode == "ghost":
        Ghost = True
    elif service_mode == "live":
        Ghost = False

    orig_input, orig_exit = builtins.input, sys.exit
    orig_stdin = sys.stdin
    os.environ["FLOW_REAL_TIME"] = "TRUE" if Real_Time else "FALSE"

    try:
        # --- 1. RESOLUTION ---
        if _func_ref:
            target_func = _func_ref
            caller_file = os.environ.get("FLOW_CURRENT_SCRIPT")
        else:
            caller_frame = inspect.stack()[1].frame
            target_func = caller_frame.f_globals.get(func_name)
            caller_file = os.path.normpath(
                os.path.abspath(inspect.stack()[1].filename)
            )
            os.environ["FLOW_CURRENT_SCRIPT"] = caller_file

        if not target_func:
            log.error(f"❌ Resolution Failed: '{func_name}' not found.")
            return

        sync_id = os.environ.get("FLOW_SYNC_ID", "DEV_SESH")

        # --- 2. EXECUTION BRANCHING ---
        if Ghost:
            # --- GHOST SCOUT PASS ---
            Flow.mode = 0
            Flow.pulse("SYSCALL: REFRESH", node_type="refresh")
            time.sleep(0.02)

            log.info(f"🔎 [GHOST SCOUT] Mapping {func_name}...")
            os.environ["FLOW_MODE"] = "SIMULATION"

            secure_gate(mode="SIMULATION")

            # Patch time.sleep → instant so the user's code doesn't block the ghost pass.
            _orig_sleep = time.sleep
            time.sleep = lambda s=0: None

            # Reset every ghost-pass guard before each new run.
            _ghost_call_count[0] = 0
            _ghost_input_count[0] = 0
            _ghost_aborted[0] = False
            _ghost_start_time[0] = time.time()

            # Build mock params respecting each parameter's annotation
            sig = inspect.signature(target_func)
            mock_params = {
                n: generate_ball(p.annotation) for n, p in sig.parameters.items()
            }

            try:
                sys.settrace(trace_calls)
                target_func(**mock_params)
            except Exception as e:
                msg = str(e)
                if (
                    "Ghost Exit Trap" in msg
                    or "Ghost Input Trap" in msg
                ):
                    log.info(f"ℹ️ Ghost pass bailed out cleanly ({msg}).")
                else:
                    log.debug(f"ℹ️ Ghost Scout path break: {e}")
            finally:
                sys.settrace(None)
                time.sleep = _orig_sleep  # Always restore real sleep

        else:
            # --- LIVE EXECUTION PASS ---
            Flow.mode = 1
            log.info(f"🔥 [LIVE EXECUTION] {func_name}")
            os.environ["FLOW_MODE"] = "LIVE"

            sys.stdin = orig_stdin
            builtins.input, sys.exit = orig_input, orig_exit
            secure_gate(mode="LIVE")

            actual_params = _params if _params else {}
            FlowEngine.run_target(caller_file, target_func, sync_id, params=actual_params)

    except Exception as e:
        log.error(f"💥 Critical Launch Failure: {e}")
    finally:
        builtins.input, sys.exit = orig_input, orig_exit


if __name__ == "__main__":
    sid = os.environ.get("FLOW_SYNC_ID", "SERVICE_SESSION")
    FlowEngine.ignite_from_service(sid)
