# DebugFlow — NeuralFlow Logic Engine

Real-time execution tracer and visual HUD for Python scripts.

## Project Structure

```
src/debugflow/
    __init__.py          — package entry, exposes `log`
    flow_bridge.py       — Flow class: socket comms to HUD, type-gate validation
    flow_engine.py       — FlowEngine + trace_calls + launch() entrypoint
    flow_service.py      — FlowSentinel + ChordWatcher: hotkey daemon, HUD lifecycle
    flow_hud.py          — Dear PyGui HUD window
    animation.py         — Animator: spine, pulses, ripples
    logger_system.py     — File-only logger (no terminal pollution)
scripts/
    build_demo_gifs.py   — Pillow-only demo GIF generator (no GUI required)
tests/
    test_chord_watcher.py — Headless unit tests for the hotkey state machine
run.py                   — Package verification + Replit workflow entry point
test.py                  — Dev test script
images/
    image.png            — Real HUD ghost-pass snapshot
    demo_pulse.gif       — README demo: live call flow
    demo_returns.gif     — README demo: return + exception flow
    demo_hover.gif       — README demo: hover metadata overlay
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

### Hotkey Configuration (flow_service.py)
Both hotkeys are env-configurable so users can dodge editor conflicts:

* `FLOW_HUD_HOTKEY`     — toggle HUD open/close   (default `<ctrl>+<alt>+f`)
* `FLOW_TRIGGER_HOTKEY` — fire engine ignite      (default `<ctrl>+<alt>+s`)

The trigger default is intentionally NOT plain `Ctrl+S` — that's bound by every
mainstream editor (VS Code / Sublime / IntelliJ / Notepad++) and the editor's
"Save" handler runs first, making the trigger feel dead. `Ctrl+Alt+S` avoids
that. Format follows pynput's HotKey grammar (e.g. `<ctrl>+<alt>+h`, `<f5>`).
Invalid grammar is caught and logged with an actionable message instead of
silently killing the sentinel.

### Per-Hotkey Debounce (flow_service.py)
`toggle_hud` and `log_save_event` each have their own `last_*_time` counter
(previously they shared one, which silently dropped the second of any two
hotkeys pressed within 0.7s of each other). The toggle debounce is widened
to 1.2s because pynput on Windows can re-detect `Ctrl+Alt+<letter>` as the
user releases keys in non-uniform order — a wide guard prevents the second
fire from instantly closing the HUD that the first fire just opened.

### ChordWatcher (flow_service.py)
Custom replacement for `pynput.GlobalHotKeys`. Two real-world bugs forced this:

1. **Stuck letter after focus steal.** When Ctrl+Alt+F opens the HUD, the OS
   delivers the F key-up to the new HUD window instead of pynput's hook.
   `GlobalHotKeys` then keeps F as "still pressed" — the *next* time the
   user holds Ctrl+Alt for any other shortcut, the stale F instantly
   re-fires the HUD chord. ChordWatcher fixes this by clearing the
   non-modifier keys of a chord from its own `pressed` set the moment the
   chord fires, regardless of what release events arrive later.

2. **Ctrl/Alt-mangled letter never matches.** With Ctrl held, Windows sends
   F as `KeyCode(char='\\x06', vk=70)`. `char.lower()` gives `"\\x06"`, which
   never matches the parsed chord `{"ctrl", "alt", "f"}` — the chord
   silently never fires (the user-reported regression). Two safety nets:
   * Run every event through `Listener.canonical(key)` so pynput un-mangles
     it via the OS keymap.
   * If `.char` is still `None` or non-printable, fall back to the virtual
     key code (`.vk`) using a fixed `_VK_TO_NAME` map (A–Z, 0–9, F1–F24).
   The listener is constructed and assigned to `self._listener` *before*
   `start()` so even the very first press has a canonicaliser available.

Covered by `tests/test_chord_watcher.py` — 12 headless tests synthesising
both the clean Windows case and the mangled-char case, including the
two-chord interleave bug from (1) above. Tests stub out pynput entirely
so they run in any environment, including the headless Replit container.

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

### Project-Rooted Logs (flow_service.py, logger_system.py)
`flow activate` captures `os.getcwd()` into `FLOW_PROJECT_ROOT` and propagates
it to the sentinel/HUD/engine subprocesses via `env=`. `logger_system.py`
reads that env var first when picking `_LOG_DIR`, falling back to `Path.cwd()`
only for direct dev usage. This pins logs to the user's project even when
detached pythonw.exe processes inherit `C:\Users\<name>` as their cwd.

### Per-Node Timing (flow_engine.py → flow_bridge.py → flow_hud.py)
`trace_calls` stamps `time.perf_counter()` into `_call_start_ts[id(frame)]` on
every `call` event, then pops it on the matching `return` / `exception` to
compute `duration_ms` (rounded to 0.01ms). The bridge ships it in the payload
and the HUD stores it on the node, surfacing it only on hover so the
non-hovered view stays clean.

### Click-to-Source (flow_hud.py)
Every node carries `file` (absolute path) and `line` (`co_firstlineno`)
captured from the live frame in `trace_calls`. A DPG mouse-click handler
hit-tests the click against visible node centers (radius 25px, ignoring the
DragHandle) and calls `open_in_editor(file, line)` on a daemon thread so a
slow editor spawn never stalls the UI loop. The launcher tries, in order:
`FLOW_EDITOR_CMD` env override → `code -g` → `cursor -g` → `windsurf -g` →
`subl` → `pycharm --line` → OS-level open as final fallback.

### Hover Metadata Overlay (flow_hud.py)
Reuses the existing `dist < 45` hover trigger that already drives node alpha.
When triggered, draws a single `module · duration` line at size 12 (vs 14/16
for primary content) in dim cyan `[120, 200, 230]`, sitting just below the
`returns` label. No new theme bindings, no new fonts — purely a smaller,
dimmer use of the existing palette so it reads as metadata.

## Dependencies

```
dearpygui>=2.0,<3   — HUD rendering (Windows/Mac GUI)
pynput>=1.7.6       — System-wide hotkeys (cross-platform, no root needed)
psutil>=5.9         — Process management
```

These are declared in `pyproject.toml`'s `[project].dependencies`. An earlier
release shipped with `dependencies = []`, which made `pip install debugflow`
silently land an unimportable package on user machines — the wheel installed
fine but `flow activate` blew up on the first `import psutil`. Always run the
packaging smoke test before tagging a release:

```bash
python -m build --no-isolation
pip install --target /tmp/wc --no-deps dist/debugflow-*.whl
PYTHONPATH=/tmp/wc python -c "from debugflow.flow_service import main, ChordWatcher"
```

## Packaging Notes

* `pyproject.toml` is the single source of truth. The legacy `setup.py`
  (which declared `version="0.1.0"` while pyproject said `1.0.1`) was
  removed — having both was a recipe for "which file did pip read?" bugs.
* Runtime state files (`.hud_pid`, `.engine_pid`, `.die`) are excluded from
  the wheel via `[tool.setuptools.exclude-package-data]`, so the wheel
  doesn't ship the build machine's stale PIDs to every install.
* Build the wheel + sdist with `python -m build --no-isolation` (the
  `--no-isolation` flag avoids a Replit-specific failure where the build
  bootstrap can't resolve its own `setuptools`/`wheel` requirements).

## Running on Replit

The GUI and hotkeys don't function in the Replit cloud environment (Linux, no display, no root for keyboard).  
The workflow (`run.py`) purely verifies the package imports correctly.  
Actual HUD usage is intended on a local Windows machine.
