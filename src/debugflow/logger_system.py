import logging
import os
from pathlib import Path

class LoggerSystem:
    _BASE_NAME = "debugflow"

    @staticmethod
    def setup():
        env_mode = os.environ.get("FLOW_DEBUG", "").upper()
        logger = logging.getLogger(LoggerSystem._BASE_NAME)
        
        # --- THE OFF SWITCH ---
        if not env_mode:
            logger.addHandler(logging.NullHandler())
            logger.propagate = False 
            return logger

        # --- THE ON SWITCH (File Only) ---
        level = logging.DEBUG if env_mode == "RAGE" else logging.INFO
        logger.setLevel(level)
        logger.handlers.clear()

        # Find Project Root (X:\DebugFlow\)
        current_file = Path(__file__).resolve()
        project_root = current_file.parent.parent.parent 
        
        log_dir = project_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "debugflow.log"

        # File Handler ONLY - No StreamHandler (Terminal stays clean)
        fh = logging.FileHandler(log_file, mode='a', encoding='utf-8')
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s"))
        
        logger.addHandler(fh)
        
        # Prevent logs from leaking to the root logger/terminal
        logger.propagate = False
        return logger

log = LoggerSystem.setup()