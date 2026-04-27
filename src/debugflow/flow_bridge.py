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

                    # GATE 1 — Input type check (fires on 'processing' events)
                    if isinstance(params, dict):
                        for key, val in params.items():
                            if key not in sig.parameters:
                                continue
                            expected = sig.parameters[key].annotation
                            # Skip unannotated params
                            if expected is inspect.Parameter.empty:
                                continue
                            if not isinstance(val, expected):
                                final_type = "nuke"
                                want = getattr(expected, "__name__", str(expected))
                                got = builtins_type(val).__name__
                                display_params = (
                                    f"CRIT_FAIL [IN]: '{key}' expected {want}, got {got}"
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
