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

PULSE_DOWN = (0, 255, 200, 255)
PULSE_UP = (120, 200, 255, 255)
SPINE_NEUTRAL = (160, 180, 200, 120)
SPINE_GLOW = (0, 255, 200, 25)

class FlowAnimator:
    def __init__(self, center_x):
        self.cx = center_x
        self.pulses = []
        self.ripples = []

    def add_pulse(self, start_y, end_y, p_type="down"):
        """Adds a new pulse to the animation queue."""
        self.pulses.append({
            "start_y": start_y,
            "end_y": end_y,
            "t": 0.0,
            "type": p_type
        })

    def update_and_draw(self, canvas, scroll_offset, node_positions):
        """
        The main tick function. 
        Pass your 'NerveCanvas' tag and current HUD scroll_offset.
        """
        curr_time = time.time()
        
        # 1. Draw Static Spine
        self._draw_spine(canvas, scroll_offset, node_positions)
        
        # 2. Draw Ripples
        self._draw_ripples(canvas, scroll_offset)
        
        # 3. Draw Nodes (The 'Beat' Effect)
        self._draw_node_beats(canvas, scroll_offset, node_positions, curr_time)
        
        # 4. Update and Draw Pulses (The 'Comet' Effect)
        self._draw_pulses(canvas, scroll_offset)

    def _draw_spine(self, canvas, scroll, nodes):
        for i in range(len(nodes) - 1):
            p1 = [nodes[i][0], nodes[i][1] + scroll]
            p2 = [nodes[i+1][0], nodes[i+1][1] + scroll]
            
            # Hybrid Spine
            dpg.draw_line(p1=p1, p2=p2, color=SPINE_NEUTRAL, thickness=2, parent=canvas)
            dpg.draw_line(p1=p1, p2=p2, color=SPINE_GLOW, thickness=4, parent=canvas)

    def _draw_node_beats(self, canvas, scroll, nodes, t):
        beat = (math.sin(t * BEAT_SPEED) * BEAT_STRENGTH) + 1.0
        for (nx, ny) in nodes:
            y_off = ny + scroll
            # Glow
            dpg.draw_circle(center=(nx, y_off), radius=10 * beat, 
                           color=(0, 255, 180, 40), fill=(0, 255, 180, 20), parent=canvas)
            # Core
            dpg.draw_circle(center=(nx, y_off), radius=4, 
                           color=(255, 255, 255, 255), fill=(255, 255, 255, 255), parent=canvas)

    def _draw_pulses(self, canvas, scroll):
        for p in self.pulses[:]:
            p["t"] += PULSE_SPEED
            
            bias = -OFFSET if p["type"] == "down" else OFFSET
            draw_x = self.cx + bias
            
            # Interpolate Y
            y_raw = p["start_y"] + (p["end_y"] - p["start_y"]) * p["t"]
            y_final = y_raw + scroll
            color = PULSE_DOWN if p["type"] == "down" else PULSE_UP

            # Comet Tail (5 segments)
            for k in range(5):
                t_offset = p["t"] - k * 0.04
                if t_offset < 0: continue
                
                y_tail = (p["start_y"] + (p["end_y"] - p["start_y"]) * t_offset) + scroll
                alpha = int(200 * (1 - k/5))
                dpg.draw_circle(center=(draw_x, y_tail), radius=2.5 - k*0.3,
                               color=(*color[:3], alpha), fill=(*color[:3], alpha//2), parent=canvas)

            # Head
            dpg.draw_circle(center=(draw_x, y_final), radius=3, color=color, fill=color, parent=canvas)

            # Ripple Trigger (Fire at start of movement)
            if p["t"] < 0.1: # Continuous small ripples while moving
                 self.ripples.append({"y": y_raw, "life": 0.0, "color": color})

            if p["t"] >= 1.0:
                self.pulses.remove(p)

    def _draw_ripples(self, canvas, scroll):
        for r in self.ripples[:]:
            r["life"] += RIPPLE_EXPANSION
            alpha = int(160 * (1 - r["life"]))
            spread = 2 + r["life"] * 14

            if alpha <= 0:
                self.ripples.remove(r)
                continue

            draw_y = r["y"] + scroll
            dpg.draw_line(p1=(self.cx - spread, draw_y), p2=(self.cx + spread, draw_y),
                         color=(*r["color"][:3], alpha), thickness=1.5, parent=canvas)