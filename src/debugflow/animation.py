"""
File summary: Animation system for DebugFlow HUD — spine, pulses, ripples, node beats.
"""

import math
import time
import dearpygui.dearpygui as dpg

# =========================
# ANIMATION CONSTANTS
# =========================
SPACING = 110
OFFSET = 2  # Horizontal bias for pulses
PULSE_SPEED = 0.025
RIPPLE_EXPANSION = 0.06
BEAT_SPEED = 5.0
BEAT_STRENGTH = 0.2

# --- COLORS ---
PULSE_DOWN = (0, 255, 200, 255)       # Call pulse  (teal-green, going down)
PULSE_UP   = (120, 200, 255, 255)     # Return pulse (sky-blue, going up)
PULSE_REFRACTED = (180, 120, 255, 255) # Call going back up (purple)
SPINE_NEUTRAL = (160, 180, 200, 120)
SPINE_GLOW    = (0, 255, 200, 25)

# Per-state glow and core colors for the node beat effect.
# Keyed by the node's "type" field from _data_listener.
_STATE_COLORS = {
    "processing": {
        "glow": (255, 200,   0,  40),
        "core": (255, 200,   0, 255),
    },
    "nuke": {
        "glow": (255,  60,  60,  50),
        "core": (255,  60,  60, 255),
    },
    "success": {
        "glow": (  0, 255, 180,  40),
        "core": (  0, 255, 180, 255),
    },
    # ghost / unknown
    "ghost": {
        "glow": (100, 140, 180,  30),
        "core": (100, 140, 180, 255),
    },
}


def _state_colors(state: str):
    return _STATE_COLORS.get(state, _STATE_COLORS["ghost"])


class FlowAnimator:
    def __init__(self, center_x):
        self.cx = center_x
        self.pulses = []
        self.ripples = []

    def add_pulse(self, start_y, end_y, p_type="down"):
        """
        Queue a new travelling pulse.

        p_type controls the *semantic* direction ("down"=call, "up"=return).
        Visual direction (and colour) is determined at draw-time by comparing
        start_y and end_y so refracted calls (f5 → f1 going geometrically
        upward) automatically get the PULSE_REFRACTED colour without any
        extra bookkeeping.
        """
        self.pulses.append({
            "start_y": start_y,
            "end_y":   end_y,
            "t":       0.0,
            "type":    p_type,
        })

    def update_and_draw(self, canvas, scroll_offset, node_positions, node_states=None):
        """
        Main tick function.  Call once per frame.

        node_states: list of state strings ("processing", "success", "nuke", …)
                     parallel to node_positions.  When provided, beat glows use
                     the correct per-state colour instead of a fixed green.
        """
        curr_time = time.time()

        # 1. Draw Static Spine
        self._draw_spine(canvas, scroll_offset, node_positions)

        # 2. Draw Ripples
        self._draw_ripples(canvas, scroll_offset)

        # 3. Draw Nodes (The 'Beat' Effect)
        self._draw_node_beats(canvas, scroll_offset, node_positions, curr_time, node_states)

        # 4. Update and Draw Pulses (The 'Comet' Effect)
        self._draw_pulses(canvas, scroll_offset)

    def _draw_spine(self, canvas, scroll, nodes):
        for i in range(len(nodes) - 1):
            p1 = [nodes[i][0], nodes[i][1] + scroll]
            p2 = [nodes[i+1][0], nodes[i+1][1] + scroll]

            # Hybrid Spine: sharp neutral line + faint wide glow
            dpg.draw_line(p1=p1, p2=p2, color=SPINE_NEUTRAL, thickness=2, parent=canvas)
            dpg.draw_line(p1=p1, p2=p2, color=SPINE_GLOW,    thickness=4, parent=canvas)

    def _draw_node_beats(self, canvas, scroll, nodes, t, node_states=None):
        beat = (math.sin(t * BEAT_SPEED) * BEAT_STRENGTH) + 1.0
        for i, (nx, ny) in enumerate(nodes):
            y_off = ny + scroll

            # Pick colour based on live state — default to success (green)
            # only when no state information is available.
            state = (node_states[i] if node_states and i < len(node_states) else "success")
            sc = _state_colors(state)
            glow_c = sc["glow"]
            core_c = sc["core"]

            # Outer glow ring (breathing)
            dpg.draw_circle(
                center=(nx, y_off),
                radius=10 * beat,
                color=glow_c,
                fill=glow_c,
                parent=canvas,
            )
            # Inner core (white centre dot, overridden by HUD's state-coloured
            # circle drawn afterwards — keeps the two layers cleanly separated)
            dpg.draw_circle(
                center=(nx, y_off),
                radius=4,
                color=core_c,
                fill=core_c,
                parent=canvas,
            )

    def _draw_pulses(self, canvas, scroll):
        for p in self.pulses[:]:
            p["t"] += PULSE_SPEED

            # --- Visual direction detection ---
            # If start_y > end_y the pulse travels geometrically upward.
            # A "down" (call) pulse going upward is a refracted cross-call
            # (e.g. f5 calling f1 again).  Use a distinct colour so the user
            # can see "this is a call but it jumps backward in the spine."
            going_up = p["start_y"] > p["end_y"]
            if p["type"] == "up":
                color = PULSE_UP
            elif going_up:
                color = PULSE_REFRACTED   # call travelling upward (refracted)
            else:
                color = PULSE_DOWN        # normal downward call

            bias = -OFFSET if p["type"] == "down" else OFFSET
            draw_x = self.cx + bias

            # Interpolate Y position along the travel path
            y_raw   = p["start_y"] + (p["end_y"] - p["start_y"]) * p["t"]
            y_final = y_raw + scroll

            # Comet Tail (5 segments fading behind the head)
            for k in range(5):
                t_offset = p["t"] - k * 0.04
                if t_offset < 0:
                    continue
                y_tail = (p["start_y"] + (p["end_y"] - p["start_y"]) * t_offset) + scroll
                alpha  = int(200 * (1 - k / 5))
                dpg.draw_circle(
                    center=(draw_x, y_tail),
                    radius=2.5 - k * 0.3,
                    color=(*color[:3], alpha),
                    fill=(*color[:3], alpha // 2),
                    parent=canvas,
                )

            # Head
            dpg.draw_circle(center=(draw_x, y_final), radius=3,
                            color=color, fill=color, parent=canvas)

            # Ripple at the leading edge (first ~10 % of travel)
            if p["t"] < 0.1:
                self.ripples.append({"y": y_raw, "life": 0.0, "color": color})

            if p["t"] >= 1.0:
                self.pulses.remove(p)

    def _draw_ripples(self, canvas, scroll):
        for r in self.ripples[:]:
            r["life"] += RIPPLE_EXPANSION
            alpha  = int(160 * (1 - r["life"]))
            spread = 2 + r["life"] * 14

            if alpha <= 0:
                self.ripples.remove(r)
                continue

            draw_y = r["y"] + scroll
            dpg.draw_line(
                p1=(self.cx - spread, draw_y),
                p2=(self.cx + spread, draw_y),
                color=(*r["color"][:3], alpha),
                thickness=1.5,
                parent=canvas,
            )
