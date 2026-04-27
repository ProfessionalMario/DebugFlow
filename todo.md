Project Dossier: NeuralFlow HUD (v1.0)
Role: Expert Assistant / AI Engineer
Context: Real-time execution tracer for ML/AI workflows.

1. Project Vision & Goal
NeuralFlow is a high-performance, "stealth-style" Headless HUD (Heads-Up Display) built using Dear PyGui. It provides real-time visual feedback for code execution by tracing function calls, parameters, and return values without interrupting the developer's primary workspace. It is designed to look like a "cybernetic spine" or "nerve canvas" on the side of the screen.

2. Core Architecture
The system operates on a Producer-Consumer model across three main threads:

The Hook (Producer): Injects into the target code (e.g., via Ctrl+S or function decorators) and sends JSON payloads via Sockets.

Socket Listener: A background thread in the HUD that listens on Port 5555. It uses a 0.5s timeout to prevent "Zombie Processes" (pythonw.exe hanging).

UI Engine (Consumer): A dedicated thread running the Dear PyGui run() loop, managing the GPU-accelerated drawing canvas.

3. Module Breakdown
FlowHUD (Main Class): Manages the node_map (history of calls) and pulses (animations between nodes).

The NerveCanvas: A raw drawlist where all geometry (lines, circles, text) is rendered manually every frame.

The Spine Logic: A vertical coordinate system (y_cursor) that manages node placement.

Smooth Scroll Engine: Uses interpolation (target_scroll vs scroll_offset) to "chase" the active execution point, keeping the newest nodes in the "Focus Zone."

4. Visual & Interaction Specs
Aesthetics: High-alpha backgrounds, cool-toned (cyan/teal) glow effects, and "heartbeat" animations for nodes.

Dynamic Spacing: NODE_SPACING (currently 110px) controls the vertical gap.

The "Focus Shift": Once the 5th node is spawned, the entire canvas slides up to keep the active work at the center-line.

Deduplication Gate: A 300ms window that prevents "AAA BBBB" spam from aggressive keyboard triggers by checking a unique signature of NodeName + Params.

5. Current Implementation Status
[FIXED] Zombie Processes: Added socket.settimeout and os._exit(0) to ensure the pythonw.exe process dies fully on exit.

[FIXED] Coordinate Drift: Implemented a "Hard Reset" in the SYSCALL: REFRESH interceptor to force y_cursor, target_scroll, and scroll_offset back to zero.

[IN PROGRESS] Formatting: Moving from simple type-strings (int, str) to "Deep Inspection" (e.g., Tensor[1, 256] or List[12]).

6. Identified Issues
Socket Persistence: Port 5555 can sometimes remain "Busy" if the crash is hard enough (using SO_REUSEADDR to mitigate).

Text Overflow: Very long parameter strings or return values can bleed off the edge of the 400px window.

Refresh Aggression: High-speed refreshes during rapid code saving can cause a momentary flicker before the reset clears.

7. Future Roadmap
Strict Formatting: Implement truncation rules for complex ML objects (Tensors, DataFrames) so they remain legible within the HUD width.

Nuke Animations: Add high-impact red animations for "Exception" or "Nuke" type nodes.

Persistence Layer: Allow the HUD to save the current "Spine" to a JSON log before clearing.

Deployment: Pack the project using Nuitka for C++ level performance and source code obfuscation.