import socket
import json
import traceback
from . import log
import uuid
import os
import inspect


class Flow:
    _instance = None
    _connected = False
    run_id = None
    mode = 0

    def __init__(self, host="127.0.0.1", port=5555):
        self.host = host
        self.port = port
        Flow._instance = self

    @staticmethod
    def init(sync_id):
        """
        The Shielded Init: Safely establishes the synapse connection.
        """
        try:
            os.environ["FLOW_SYNC_ID"] = sync_id
            with socket.create_connection(("127.0.0.1", 5555), timeout=0.5):
                Flow._connected = True
        except (ConnectionRefusedError, socket.timeout):
            Flow._connected = False
            print("  ⚠️  [SYNAPSE OFFLINE]: HUD not detected. Running in headless mode.")
        except Exception as e:
            Flow._connected = False
            print(f"  🛑  [INIT_FAILURE]: NeuralFlow could not bridge. Error: {str(e)[:50]}...")

    @staticmethod
    def pulse(node, params=None, returns=None, node_type="pulse"):
        """
        Send an execution event to the HUD.

        Args:
            node:       A callable or a string name (e.g. a function object or "SYSCALL: REFRESH").
            params:     Dict of {param_name: value} captured at call-time, or a plain string/None.
            returns:    The actual return value of the function, or None.
            node_type:  One of "processing", "success", "nuke", "refresh", "pulse".

        Returns:
            None. Failures are silently swallowed so they never interrupt the traced script.
        """
        try:
            if Flow._instance is None:
                Flow()

            node_name = node.__name__ if callable(node) else str(node)
            final_type = node_type
            display_params = params
            display_returns = returns

            # --- DUAL-GATE TYPE VALIDATION ---
            # Only runs when node is a callable so we can inspect its signature.
            if callable(node):
                try:
                    sig = inspect.signature(node)

                    # GATE 1 — Input checks (fires on 'processing' events)
                    if isinstance(params, dict):

                        # 1a. PARAM COUNT — detect unexpected / extra arguments.
                        # We compare the keys actually passed against the declared
                        # parameters, ignoring *args / **kwargs collectors.
                        declared = {
                            n for n, p in sig.parameters.items()
                            if p.kind not in (
                                inspect.Parameter.VAR_POSITIONAL,   # *args
                                inspect.Parameter.VAR_KEYWORD,      # **kwargs
                            )
                        }
                        unexpected = [k for k in params if k not in declared]
                        if unexpected:
                            final_type = "nuke"
                            display_params = (
                                f"CRIT_FAIL [COUNT]: unexpected param(s): "
                                f"{', '.join(unexpected)}"
                            )
                        else:
                            # 1b. TYPE CHECK — validate each annotated param.
                            for key, val in params.items():
                                if key not in sig.parameters:
                                    continue
                                expected = sig.parameters[key].annotation
                                # Skip unannotated params — no annotation = no contract
                                if expected is inspect.Parameter.empty:
                                    continue
                                if not isinstance(val, expected):
                                    final_type = "nuke"
                                    want = getattr(expected, "__name__", str(expected))
                                    got = builtins_type(val).__name__
                                    display_params = (
                                        f"CRIT_FAIL [TYPE]: '{key}' expected "
                                        f"{want}, got {got}"
                                    )
                                    break

                    # GATE 2 — Return type check (fires on 'success' events)
                    if returns is not None:
                        expected_ret = sig.return_annotation
                        if expected_ret is not inspect.Parameter.empty:
                            if not isinstance(returns, expected_ret):
                                final_type = "nuke"
                                want_ret = getattr(expected_ret, "__name__", str(expected_ret))
                                got_ret = builtins_type(returns).__name__
                                display_returns = (
                                    f"CRIT_FAIL [OUT]: expected {want_ret}, got {got_ret}"
                                )

                except Exception:
                    pass

            # --- DISPLAY FORMATTING ---
            # Render `type=value` (or `name: type=value` for dict params) so the
            # HUD shows both the data type and its actual value at every node.
            # Skip this when the type-gate already replaced the field with a
            # CRIT_FAIL message (those are ready-to-display strings).
            if final_type != "nuke":
                if isinstance(display_params, dict):
                    display_params = (
                        _format_params_dict(display_params) if display_params else None
                    )
                elif display_params is not None and not isinstance(display_params, str):
                    display_params = _format_typed_value(display_params)

                if display_returns is not None and not isinstance(display_returns, str):
                    display_returns = _format_typed_value(display_returns)

            payload = {
                "run_id": str(Flow.run_id or "DEV_SESH"),
                "flow_mode": os.environ.get("FLOW_MODE", "SIMULATION"),
                "node": node_name,
                "params": display_params,
                "returns": display_returns,
                "type": final_type,
            }

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.1)
                s.connect((Flow._instance.host, Flow._instance.port))
                s.sendall(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

        except Exception:
            pass

    @staticmethod
    def nuke(node_name, error_msg):
        """Convenience shortcut for sending a nuke (failure) event."""
        Flow.pulse(node_name, params=error_msg, node_type="nuke")

    def start_run(self, entry_func, *args, **kwargs):
        self.run_id = uuid.uuid4().hex[:8]
        log.info(f"[FLOW] Starting run_id = {self.run_id}")
        env = os.environ.copy()
        env["FLOW_RUN_ID"] = self.run_id
        return entry_func(*args, flow=self, **kwargs)


# Keep a safe reference to the real built-in type() so it is never shadowed
builtins_type = type


# --- DISPLAY HELPERS ---
# Truncate to keep HUD nodes legible inside the 400px window.
_MAX_VAL_CHARS = 24
_MAX_FIELD_CHARS = 60


def _short_repr(val):
    """A bounded repr that won't bleed off the side of the HUD."""
    try:
        s = repr(val)
    except Exception:
        s = f"<unrepr {builtins_type(val).__name__}>"
    if len(s) > _MAX_VAL_CHARS:
        s = s[: _MAX_VAL_CHARS - 1] + "…"
    return s


def _format_typed_value(val):
    """Render a single value as 'type=value' (e.g. 'int=5', 'str="hi"')."""
    type_name = builtins_type(val).__name__
    return f"{type_name}={_short_repr(val)}"


def _format_params_dict(params):
    """
    Render a {param_name: value} dict as 'name: type=value, name2: type=value'.
    Truncated overall so it fits inside the HUD label area.
    """
    parts = [f"{k}: {_format_typed_value(v)}" for k, v in params.items()]
    out = ", ".join(parts)
    if len(out) > _MAX_FIELD_CHARS:
        out = out[: _MAX_FIELD_CHARS - 1] + "…"
    return out
