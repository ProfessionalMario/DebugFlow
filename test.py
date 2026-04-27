import os
import logging
from debugflow import flow_engine

# 1. Force the environment to RAGE mode for this test
os.environ["FLOW_DEBUG"] = "RAGE"

# 2. Import the main log and the helper from your package
from debugflow import log, get_logger

# Simulate a "Bridge" module
bridge_log = get_logger("bridge")

# Simulate an "Engine" module
engine_log = get_logger("engine")

def run_test():
    print("\n--- STARTING NAMESPACE TEST ---\n")
    
    log.info("This is a general system message.")
    
    engine_log.info("Engine is initializing...")
    bridge_log.debug("Bridge is attempting to locate hardware...")
    
    try:
        # Simulate a "tantrum"
        raise ValueError("Hardware not found!")
    except Exception as e:
        bridge_log.error(f"Bridge failure: {e}")
        engine_log.critical("Engine shutting down due to bridge error.")

    print("\n--- TEST COMPLETE: Check the terminal and logs/debugflow.log ---")

if __name__ == "__main__":
    flow_engine.launch("run_test")