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
        try:
            ignore = [
                "flow_service.py",
                "flow_engine.py",
                "logger_system.py",
                "flow_hud.py",
                "flow_bridge.py",
            ]
            project_files = [
                f for f in os.listdir(".") if f.endswith(".py") and f not in ignore
            ]

            if not project_files:
                log.error("❌ No user scripts found.")
                return

            target_script = max(project_files, key=os.path.getmtime)
            log.info(f"🔥 Auto-booting: {target_script}")

            os.environ["FLOW_SYNC_ID"] = sync_id

            spec = importlib.util.spec_from_file_location(
                "__main__", os.path.abspath(target_script)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as e:
            log.critical(f"💥 Engine Discovery Crash: {e}")

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

            # Capture the live local variables at call time.
            # These are the actual param values the function received, enabling
            # accurate type-gate validation inside Flow.pulse.
            live_params = dict(frame.f_locals)
            Flow.pulse(fn_obj or func_name, params=live_params, node_type="processing")
            if not is_rt and not is_ghost:
                # Cinematic delay only in live mode — ghost runs instantly
                time.sleep(1.0)

        elif event == "exception":
            os.environ["FLOW_BLAST_CRITICAL"] = "TRUE"
            Flow.pulse(fn_obj or func_name, node_type="nuke")
            return None

        elif event == "return":
            # arg is the actual return value from Python's trace protocol
            Flow.pulse(fn_obj or func_name, returns=arg, node_type="success")
            if not is_rt and not is_ghost:
                # Cinematic delay only in live mode
                time.sleep(0.5)

    return trace_calls


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
    return mapping.get(annotation, None)


def launch(func_name, Ghost=True, Real_Time=True, _func_ref=None, _params=None):
    """
    The master airlock — runs one Ghost Scout pass to map the logic flow,
    then optionally hands off to a live execution pass.

    Args:
        func_name:  Name of the function to trace (string, looked up in caller's globals).
        Ghost:      If True, run the Ghost Scout pass with mock params first.
        Real_Time:  If True, cinematic delays are skipped; execution runs at CPU speed.
        _func_ref:  Internal — pre-resolved function object (used on recursive handover).
        _params:    Internal — mock params carried from Ghost → Live handover.

    Returns:
        None
    """
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

            log.info(f"🔎 [PASS 1/2] GHOST SCOUT: Mapping {func_name}...")
            os.environ["FLOW_MODE"] = "SIMULATION"

            secure_gate(mode="SIMULATION")

            # Patch time.sleep → instant so the user's code doesn't block the ghost pass.
            # Any sleep call in user code would freeze the ghost run for real seconds,
            # defeating the purpose of a fast non-destructive dry run.
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
                # Known soft traps — these are how we tame infinite loops and
                # `while True: input()` patterns. Not real errors; just log low.
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
            log.info(f"🔥 [PASS 2/2] LIVE EXECUTION: {func_name}")
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
