import sys
import os
import traceback
import json
from . import log
from .flow_service import FlowSentinel
from .flow_engine import _safe_serialize
from pathlib import Path

class SpineLink(FlowSentinel):
    """
    Direct-to-Log Bridge. No JSON side-cars.
    """
    def __init__(self):
        super().__init__()
        log.info("🧠 SPINE-LINK: Neural Bridge Active (Log-Only Mode).")
        self.habitat = None

    def get_llm_payload(self, exc_type, exc_value, tb):
        """
        The Forensic Handshake. 
        Combines the Traceback, the Live Frame, and the Spine Context.
        """
        # 1. Reach the actual crash site in the stack
        last_tb = tb
        while last_tb.tb_next:
            last_tb = last_tb.tb_next
        frame = last_tb.tb_frame

        # 2. Extract Surgical Telemetry
        # We grab variables but cap their size to prevent LLM context overflow
        clean_locals = {}
        for k, v in frame.f_locals.items():
            if k.startswith('__'): continue 
            clean_locals[k] = self._get_shape_or_type(v)

        # 3. Fetch the 'Neighborhood' pulses from the log
        # Since we aren't touching Flow.py, we read the tail of the log
        # which contains the [pulse] events sent just before the crash.
        recent_pulses = self.harvest_last_failure_from_logs()

        return {
            "error_report": {
                "type": exc_type.__name__,
                "message": str(exc_value),
                "site": f"{Path(frame.f_code.co_filename).name}:{frame.f_lineno}"
            },
            "forensics": {
                "local_vars": clean_locals,
                "recent_pulse_stream": recent_pulses 
            },
            "directives": "Follow foundation.md rules strictly."
        }

    def _get_shape_or_type(self, val):
        """Value-aware metadata extraction."""
        if hasattr(val, 'shape'):
            return f"Tensor/Array(shape={val.shape})"
        if isinstance(val, (int, float, bool)) or (isinstance(val, str) and len(val) < 50):
            return f"{type(val).__name__}(value={val})"
        if isinstance(val, (list, dict)):
            return f"{type(val).__name__}(len={len(val)})"
        return str(type(val).__name__)
    def harvest_last_failure_from_logs(self):
        """
        Parses debugflow.log instead of a JSON file.
        """
        log_path = os.path.join(os.getcwd(), "logs", "debugflow.log")
        if not os.path.exists(log_path):
            return "No logs found."

        with open(log_path, "r") as f:
            lines = f.readlines()
            # Grab the last 20 lines to find the [ERROR] block
            recent_logs = "".join(lines[-20:])
            return recent_logs

    def apply_patch(self, target_file, code_block):
        """Writes the LLM's fix and refreshes the Ghost map."""
        log.warning(f"🛠️  SPINE-LINK: Patching {os.path.basename(target_file)}")
        try:
            with open(target_file, "w") as f:
                f.write(code_block)
            self.ignite_ghost_pipeline()
            log.info("✅ Patch applied. HUD should refresh automatically.")
        except Exception as e:
            log.error(f"❌ Patch failed: {e}")

            
# --- Test Guard ---
if __name__ == "__main__":
    # This allows you to verify SpineLink standalone
    linker = SpineLink()
    print("SpineLink Module Ready for LLM integration.")