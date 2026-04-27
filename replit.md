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
| `flow-logs on` | Enable file logging persistently |
| `flow-logs off` | Disable file logging (complete silence) |
| `flow-logs status` | Show current logging state |
| `python run.py` | Replit workflow — verifies all modules load |

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
`keyboard` import is guarded — if unavailable (no root on Linux), the sentinel prints an actionable message and stays alive.

## Dependencies

```
dearpygui==2.3      — HUD rendering (Windows/Mac GUI)
keyboard==0.13.5    — System-wide hotkeys (needs root on Linux)
psutil==7.2.2       — Process management
```

## Running on Replit

The GUI and hotkeys don't function in the Replit cloud environment (Linux, no display, no root for keyboard).  
The workflow (`run.py`) purely verifies the package imports correctly.  
Actual HUD usage is intended on a local Windows machine.
