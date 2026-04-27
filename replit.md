# DebugFlow — NeuralFlow Logic Engine

Real-time execution tracer and visual HUD for Python scripts.

## Project Structure

```
src/debugflow/
    __init__.py          — package entry, exposes `log`
    flow_bridge.py       — Flow class: socket comms to HUD, type-gate validation
    flow_engine.py       — FlowEngine + trace_calls + launch() entrypoint
    flow_service.py      — FlowSentinel: hotkey daemon, HUD lifecycle
    flow_hud.py          — Dear PyGui HUD window
    animation.py         — Animator: spine, pulses, ripples
    logger_system.py     — File-only logger (no terminal pollution)
run.py                   — Package verification + Replit workflow entry point
test.py                  — Dev test script
```

## Entry Points

| Command | Function |
|---|---|
| `flow activate` | Toggle the NeuralFlow sentinel on/off |
| `flow status` | Print whether the sentinel daemon is currently running |
| `flow help` | Show CLI usage |
| `flow-logs on` | Enable file logging persistently |
| `flow-logs off` | Disable file logging (complete silence) |
| `flow-logs status` | Show current logging state |
| `python run.py` | Replit workflow — verifies all modules load |

> The `flow` command is a real subcommand dispatcher (`flow_service.main`).
> Bare `flow` or any unknown subcommand (`flow loggies`, `flow xyz`) prints
> usage instead of accidentally toggling the sentinel.

## Logging

State is stored in `~/.debugflow/.debug_on` (flag file).
Log output goes to `~/.debugflow/debugflow.log`.
No env vars needed — persists across restarts and reinstalls.

## Key Design Decisions

### Type Validation (flow_bridge.py)
`Flow.pulse()` has a `node_type` parameter (NOT `type` — that would shadow the built-in).  
The dual-gate compares live params/returns against the function's annotations using `inspect.signature`.  
`builtins_type` is a module-level alias to the real `type()` built-in so the gate can call it safely.

### Ghost Scout Pass (flow_engine.py)
`launch()` calls `generate_ball(annotation)` to build mock params per annotation type.  
`inspect.Parameter.empty` (unannotated params) maps to `None`.  
`trace_calls` captures `frame.f_locals` at call-time and passes them to `Flow.pulse` so the type-gate validates real values even during simulation.

### Platform Safety (flow_service.py, flow_engine.py)
`_make_flags()` returns `creationflags` only on Windows.  
On Linux/Mac the dict is empty, so `subprocess.Popen` doesn't raise `ValueError`.  
Hotkeys use `pynput.GlobalHotKeys` (cross-platform: Windows / Mac / Linux/X11)
instead of the `keyboard` library, which required root on Linux. The import is
guarded — if `pynput` can't load (e.g. no display server), the sentinel prints
an actionable message and idles instead of crashing.

### User-Input & Loop Tolerance (flow_engine.py)
The ghost (dry-run) pass is bounded three ways so a user's traced code can't
hang the HUD:

* `MAX_GHOST_CALLS` (50) — hard cap on traced `call` events per pass.
* `MAX_GHOST_SECONDS` (3.0) — wall-clock budget for the whole ghost pass.
* `MAX_GHOST_INPUTS` (5) — the patched `input()` returns `"GHOST_DATA"` for
  the first N calls, then raises `Ghost Input Trap` so `while True: input()`
  loops bail out instead of looping forever.

When any guard trips, `_stop_ghost_trace()` removes the trace hook **without**
firing a `nuke` node. The function that's still running stays in its existing
`processing` (yellow) state on the HUD, which correctly reflects "this thing
really is still in progress, we just stopped watching."

In LIVE mode `input()` and `sys.exit` are left untouched so real CLI flows
work normally.

### Display Formatting (flow_bridge.py)
`Flow.pulse()` renders params and returns as `type=value` (and dict params as
`name: type=value, name2: type=value`) before sending to the HUD, so each
node shows both the data type and the actual value. Output is bounded so it
fits inside the 400px HUD column. CRIT_FAIL strings from the type-gate are
left untouched.

## Dependencies

```
dearpygui==2.3      — HUD rendering (Windows/Mac GUI)
pynput>=1.7.6       — System-wide hotkeys (cross-platform, no root needed)
psutil==7.2.2       — Process management
```

## Running on Replit

The GUI and hotkeys don't function in the Replit cloud environment (Linux, no display, no root for keyboard).  
The workflow (`run.py`) purely verifies the package imports correctly.  
Actual HUD usage is intended on a local Windows machine.
