"""
DebugFlow — package verification and status runner.
Confirms all modules load correctly and shows the project info.
"""
import sys
import os
import time

print("\n" + "═" * 50)
print("  DebugFlow — NeuralFlow Logic Engine")
print("═" * 50)

errors = []

modules = [
    ("debugflow", "Core package"),
    ("debugflow.flow_bridge", "Flow bridge (socket comms)"),
    ("debugflow.flow_engine", "Flow engine (tracer)"),
    ("debugflow.flow_service", "Flow service (sentinel)"),
    ("debugflow.logger_system", "Logger system"),
]

for mod, desc in modules:
    try:
        __import__(mod)
        print(f"  ✔  {desc}")
    except ImportError as e:
        print(f"  ✖  {desc}  →  {e}")
        errors.append((mod, str(e)))

print("─" * 50)

if errors:
    print("  [!] Some modules failed to import.")
    print("  Run: pip install -r requirements.txt")
else:
    print("  [OK] All modules loaded successfully.")
    print()
    print("  Platform info:")
    print(f"      Python  : {sys.version.split()[0]}")
    print(f"      OS      : {sys.platform}")
    print()
    print("  To use DebugFlow on Windows:")
    print("      pip install -e .")
    print("      flow activate")
    print()
    print("  To trace a function in your script:")
    print("      from debugflow.flow_engine import launch")
    print("      launch('my_function')")

print("═" * 50)
print()

# Keep the process alive so the workflow console stays active
while True:
    time.sleep(60)
