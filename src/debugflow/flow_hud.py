import dearpygui.dearpygui as dpg
import queue
import threading
import time
import math
import sys
import os
import traceback
from . import log
import socket
import json
from .animation import FlowAnimator
# This automatically connects to your LoggerSystem because it's under 'debugflow'

WIN_HEIGHT= 850
WIN_WIDTH= 400
NODE_SPACING = 110
scroll_speed = 0.1
BG_OPACITY = 180        # Background transparency (0=clear, 255=solid)
BASE_NAME_ALPHA = 120   # Name visibility when not hovered (0-255)
LINE_THICKNESS = 2.5    # Thickness of the vertical spine line
PULSE_SIZE = 2.5        # Radius of the data 'packets' traveling the spine
PULSE_SPEED = 0.05      # Travel speed of pulses (higher = faster)

# Animation
FADE_SPEED = 8.0        # Speed of the hover 'bloom' effect
BEAT_SPEED = 6.0        # Frequency of the orb's heartbeat pulse
BEAT_STRENGTH = 0.08    # Magnitude of the heartbeat expansion (0.1 = 10%)



class FlowHUD:
    def __init__(self, ui_queue):
        log.info("Initializing Stealth HUD Engine...")
        try:
            self.ui_queue = ui_queue
            self.active_run_id = None
            self.node_map = []
            self.pulses = [] 
            self.scroll_offset = 0.0
            self.target_scroll = 0.0
            # --- 🚩 NEW MODE LOGIC ---
            self.mode = 1  # 0: GHOST, 1: LIVE
            self.mode_labels = [" Mode: GHOST RUN", " Mode: LIVE RUN"]
            # -------------------------
            self.center_x = 200
            self.y_cursor = 60 # Set to match SYSCALL: REFRESH default
            # --- SCROLL LOGIC ---
            self.scroll_speed = 15.0 # Smoothness factor
            self.last_sig = None      #For controlling Ctrl+S firing. 
            self.last_sig_time = 0     #For controlling Ctrl+S firing. 
            self.animator = FlowAnimator(center_x=200)
            dpg.create_context()
            dpg.create_viewport(
                title='DebugFlow', 
                width=WIN_WIDTH, 
                height=WIN_HEIGHT,
                x_pos=1500,  # Adjust this based on your monitor width
                y_pos=50, 
                decorated=False, 
                always_on_top=True
            )
            # Start the Socket Listener thread
            threading.Thread(target=self._socket_listener, daemon=True).start()
            log.info("HUD Socket Listener thread launched.")
            
            # Primary Window with NO SCROLLBAR
            with dpg.window(tag="PrimaryWindow", no_title_bar=True, no_scrollbar=True):
                with dpg.child_window(tag="DragHandle", height=40, border=False, no_scrollbar=True):
                    self.status_text = dpg.add_text(self.mode_labels[self.mode], color=[0, 255, 150, 180], pos=[130, 10])
                
                dpg.add_drawlist(tag="NerveCanvas", width=400, height=850)

            self._apply_styles()
            self._setup_handlers()

            dpg.setup_dearpygui()
            dpg.show_viewport()
            dpg.set_primary_window("PrimaryWindow", True)
            
            threading.Thread(target=self._data_listener, daemon=True).start()
            log.info("HUD initialized successfully.")
        except Exception as e:
            log.error(f"HUD Init Failed: {e}")

    
    def _socket_listener(self):
        try:
            log.info("[SOCKET] starting on 5555...")

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            sock.bind(('127.0.0.1', 5555))
            sock.listen(5)
            sock.settimeout(1.0)

            log.info("[SOCKET] listening OK")

            while dpg.is_dearpygui_running():
                try:
                    conn, addr = sock.accept()
                    log.info(f"[SOCKET] connection from {addr}")

                    data = conn.recv(1024)

                    if not data:
                        continue

                    decoded = data.decode('utf-8')
                    log.info(f"[HUD RECEIVED] {decoded}")

                    payload = json.loads(decoded)

                    self.ui_queue.put(payload)

                except socket.timeout:
                    continue

                except Exception as e:
                    log.error(f"[SOCKET LOOP ERROR] {repr(e)}")

        except Exception as e:
            log.error(f"[SOCKET FATAL] {repr(e)}")


    def _toggle_mode(self):
        self.mode = 1 if self.mode == 0 else 0
        dpg.set_value(self.status_text, self.mode_labels[self.mode])
        # Update color based on mode
        color = [0, 255, 150, 180] if self.mode == 0 else [0, 200, 255, 180]
        dpg.configure_item(self.status_text, color=color)
        log.info(f"HUD State Manually Swapped: {self.mode_labels[self.mode]}")


    def _manage_scrolling(self):
        try:
            # 1. Calculate content floor
            total_height = len(self.node_map) * NODE_SPACING
            
            # 2. Don't scroll further than the last node reaching the center of screen
            # This prevents the "infinite abyss"
            max_scroll_limit = min(0, -(total_height - (WIN_HEIGHT / 2)))
            self.target_scroll = max(max_scroll_limit, min(0, self.target_scroll))
            
            # 3. Smooth Chase
            self.scroll_offset += (self.target_scroll - self.scroll_offset) * scroll_speed
                
        except Exception as e:
            log.error(f"Scroll Manager Error: {e}")
    def _apply_styles(self):
        with dpg.theme() as global_theme:
            with dpg.theme_component(dpg.mvAll):
                # Add this inside the 'with dpg.theme_component(dpg.mvAll):' block
                dpg.add_theme_style(dpg.mvStyleVar_ScrollbarSize, 0)
                dpg.add_theme_style(dpg.mvStyleVar_WindowBorderSize, 0)
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (7, 7, 9, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (0, 0, 0, 0))
        dpg.bind_theme(global_theme)

    def _setup_handlers(self):
        with dpg.handler_registry():
            dpg.add_mouse_drag_handler(button=0, callback=self._drag_callback)
            # dpg.add_key_press_handler(key=dpg.mvKey_Tab, callback=self._toggle_mode)
            dpg.add_mouse_wheel_handler(callback=self._wheel_callback)

    def _wheel_callback(self, sender, app_data):
        # Update target scroll based on wheel input
        self.target_scroll += app_data * 40
        # Prevent scrolling too far up
        if self.target_scroll > 0: self.target_scroll = 0

    def _toggle_mode(self):
        self.mode = 1 if self.mode == 0 else 0
        dpg.set_value(self.status_text, self.mode_labels[self.mode])
        log.info(f"UI Mode Swapped to: {self.mode_labels[self.mode]}")

    def _drag_callback(self, sender, app_data):
        if dpg.is_item_hovered("DragHandle"):
            pos = dpg.get_viewport_pos()
            dpg.set_viewport_pos([pos[0] + app_data[1], pos[1] + app_data[2]])

    def _format_type(self, val):
        if val is None or val == {}: 
            return "void"
        if isinstance(val, dict):
            # Just show the values to keep it clean
            return ", ".join([str(v) for v in val.values()])[:30] # Cap length
        return str(val)
    
    def _data_listener(self):
        while dpg.is_dearpygui_running():
            try:
                msg = self.ui_queue.get(timeout=0.01)
                
                # --- AUTO-MODE DETECTION ---
                flow_mode = msg.get("flow_mode", "").upper()
                if flow_mode == "SIMULATION" and self.mode != 0:
                    self.mode = 0
                    dpg.set_value(self.status_text, self.mode_labels[0])
                elif flow_mode == "LIVE" and self.mode != 1:
                    self.mode = 1
                    dpg.set_value(self.status_text, self.mode_labels[1])
                    dpg.configure_item(self.status_text, color=[0, 200, 255, 180])

                node_raw_name = str(msg.get("node", "")).split('@')[0]
                msg_type = msg.get("type", "pulse")

                # --- 1. GLOBAL OVERRIDE ---
                if node_raw_name == "SYSCALL: REFRESH":
                    self.node_map = []
                    # Clear animator states too
                    self.animator.pulses = []
                    self.animator.ripples = []
                    self.target_scroll = 0.0
                    self.scroll_offset = 0.0
                    self.y_cursor = 60 
                    self.active_run_id = None
                    continue

                # --- 2. SEARCH & UPDATE (Handle States) ---
                existing_node = next((n for n in self.node_map if n["name"] == node_raw_name), None)
                if existing_node:
                    new_params = self._format_type(msg.get('params'))
                    new_returns = self._format_type(msg.get('returns'))
                    
                    if new_params != "void": existing_node["params"] = new_params
                    if new_returns != "void": existing_node["returns"] = new_returns
                    
                    # --- FIRE RETURN PULSE (UP) ---
                    if existing_node["type"] == "processing" and msg_type in ["success", "nuke"]:
                        idx = self.node_map.index(existing_node)
                        if idx > 0: 
                            # Use new animator logic
                            self.animator.add_pulse(
                                start_y=existing_node["pos"][1], 
                                end_y=self.node_map[idx-1]["pos"][1], 
                                p_type="up"
                            )

                    existing_node["type"] = msg_type
                    existing_node["current_alpha"] = 1.2 
                    continue

                # --- 3. NODE CREATION ---
                new_node = {
                    "pos": [self.center_x, self.y_cursor],
                    "name": node_raw_name,
                    "params": self._format_type(msg.get('params')),
                    "returns": self._format_type(msg.get('returns')),
                    "type": msg_type,
                    "birth": time.time(),
                    "current_alpha": 1.0
                }
                
                # --- FIRE CALL PULSE (DOWN) ---
                if self.node_map:
                    # Use new animator logic
                    self.animator.add_pulse(
                        start_y=self.node_map[-1]["pos"][1], 
                        end_y=self.y_cursor, 
                        p_type="down"
                    )

                self.node_map.append(new_node)
                
                # --- 4. THE STICKY CENTER SCROLL ---
                if self.y_cursor > 700:
                    self.target_scroll = -(self.y_cursor - 700)

                self.y_cursor += NODE_SPACING
                
            except queue.Empty: pass


    def run(self):
        if os.environ.get("FLOW_UI_ALLOWED") != "TRUE":
            sys.exit(0)
        log.info("HUD Main Loop Started")
        try:
            while dpg.is_dearpygui_running():
                try:
                    dpg.delete_item("NerveCanvas", children_only=True)
                    
                    # --- POISON PILL ---
                    kill_signal = os.path.join(os.path.dirname(__file__), ".die")
                    if os.path.exists(kill_signal):
                        log.info("HUD: Poison pill detected. Shutting down.")
                        dpg.stop_dearpygui()
                        os.remove(kill_signal)
                        sys.exit(0)

                    # --- SMOOTH SCROLL ---
                    self.scroll_offset += (self.target_scroll - self.scroll_offset) * scroll_speed
                    
                    # Prevent scrolling past the last node
                    self.target_scroll = max(min(0, -(self.y_cursor - NODE_SPACING - 425)), self.target_scroll)
                    
                    m_pos = dpg.get_mouse_pos(local=True)
                    curr_time = time.time()

                    # 1. TRIGGER ANIMATOR (Spine, Pulses, Ripples)
                    # This replaces your manual pulse/wire loops
                    node_pts = [n["pos"] for n in self.node_map]
                    self.animator.update_and_draw("NerveCanvas", self.scroll_offset, node_pts)

                    # 2. DRAW HUD OVERLAYS (Labels & State Orbs)
                    for node in self.node_map:
                        nx, ny = node["pos"]
                        ny_off = ny + self.scroll_offset
                        
                        # Culling: Only process if visible
                        if not (-100 < ny_off < WIN_HEIGHT + 100): continue

                        # Alpha & Hover Logic
                        dist = math.sqrt((m_pos[0]-nx)**2 + (m_pos[1]-ny_off)**2)
                        target_a = 1.0 if node.get("type") == "processing" or dist < 45 else 0.7
                        node["current_alpha"] += (target_a - node["current_alpha"]) * 0.1
                        m_alpha = int(node["current_alpha"] * 255)
                        
                        # State Color Mapping
                        node_type = node.get("type", "ghost")
                        if node_type == "nuke":
                            orb_c, text_accent = [255, 50, 50], [255, 100, 100, m_alpha]
                        elif node_type == "processing":
                            orb_c, text_accent = [255, 200, 0], [255, 220, 100, m_alpha]
                        elif node_type == "success":
                            orb_c, text_accent = [0, 255, 150], [0, 200, 255, m_alpha]
                        else:
                            orb_c, text_accent = [100, 100, 100], [0, 200, 255, m_alpha]

                        # Render Text Labels
                        # Params (TOP)
                        dpg.draw_text(pos=[nx - (len(node["params"])*3.8), ny_off - 28], 
                                     text=node["params"], color=text_accent, size=14, parent="NerveCanvas")
                        # Name (CENTER-RIGHT)
                        dpg.draw_text(pos=[nx + 20, ny_off - 9], 
                                     text=node["name"], color=[255, 255, 255, m_alpha], size=16, parent="NerveCanvas")
                        # Returns (BOTTOM)
                        dpg.draw_text(pos=[nx - (len(node["returns"])*3.8), ny_off + 14], 
                                     text=node["returns"], color=text_accent, size=14, parent="NerveCanvas")

                        # State Core (Overlays the Animator's beat core for state feedback)
                        dpg.draw_circle(center=[nx, ny_off], radius=5, color=[*orb_c, 255], 
                                       fill=[*orb_c, 255], parent="NerveCanvas")

                    dpg.render_dearpygui_frame()
                except Exception as e:
                    log.error(f"Frame Error: {e}")
                    time.sleep(0.01)
        finally:
            dpg.destroy_context()

            
if __name__ == "__main__":
    # Remove or comment out the 'simulate' thread entirely
    q = queue.Queue()
    FlowHUD(q).run()