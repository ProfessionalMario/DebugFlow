"""
File summary: Summary unavailable due to analysis error.
"""

import dearpygui.dearpygui as dpg
import queue
import threading
import time
import math
import sys
import os
import platform
import shutil
import subprocess
import traceback
from . import log
import socket
import json
from .animation import FlowAnimator
# This automatically connects to your LoggerSystem because it's under 'debugflow'


# --- EDITOR LAUNCHER (click-to-source) ---
# Try a list of well-known editor CLIs first, fall back to the OS-level
# 'open this file' handler. Users can override the whole thing with the
# FLOW_EDITOR_CMD env var, e.g. `FLOW_EDITOR_CMD="code -g {file}:{line}"`.
def open_in_editor(file_path, line_no):
    """
    Best-effort 'jump to file:line' for the user's editor.

    Order:
      1. FLOW_EDITOR_CMD env var (template with {file} / {line}).
      2. VS Code / Cursor / Windsurf via `code -g`.
      3. Sublime Text via `subl`.
      4. PyCharm via `pycharm --line`.
      5. OS-level open (no line number, just opens the file).
    """
    if not file_path or not os.path.exists(file_path):
        log.warning(f"Click-to-source: file not found ({file_path}).")
        return False

    line_no = int(line_no or 1)

    # 1. Explicit override.
    template = os.environ.get("FLOW_EDITOR_CMD")
    if template:
        try:
            cmd = template.format(file=file_path, line=line_no)
            subprocess.Popen(cmd, shell=True)
            log.info(f"Opened {file_path}:{line_no} via FLOW_EDITOR_CMD.")
            return True
        except Exception as e:
            log.error(f"FLOW_EDITOR_CMD failed: {e}")

    # 2-4. Auto-detect editors in priority order.
    candidates = [
        ["code", "-g", f"{file_path}:{line_no}"],
        ["cursor", "-g", f"{file_path}:{line_no}"],
        ["windsurf", "-g", f"{file_path}:{line_no}"],
        ["subl", f"{file_path}:{line_no}"],
        ["pycharm", "--line", str(line_no), file_path],
    ]
    for cmd in candidates:
        if shutil.which(cmd[0]):
            try:
                subprocess.Popen(cmd)
                log.info(f"Opened {file_path}:{line_no} via {cmd[0]}.")
                return True
            except Exception as e:
                log.warning(f"Editor {cmd[0]} failed: {e}")
                continue

    # 5. Final fallback — just open the file at the OS level.
    try:
        sys_name = platform.system()
        if sys_name == "Windows":
            os.startfile(file_path)  # type: ignore[attr-defined]
        elif sys_name == "Darwin":
            subprocess.Popen(["open", file_path])
        else:
            subprocess.Popen(["xdg-open", file_path])
        log.info(f"Opened {file_path} via OS handler (no line jump).")
        return True
    except Exception as e:
        log.error(f"All editor launchers failed: {e}")
        return False


def _format_duration(ms):
    """
    Glanceable duration label for the hover overlay.
    Uses compound whole units (e.g. ``1s 234ms``, ``2m 30s``, ``1h 5m``)
    instead of long fractional values like ``1.234567s`` so the value
    is readable at a glance regardless of magnitude.
    """
    if ms is None:
        return ""
    if ms < 1.0:
        return f"{round(ms * 1000)}\u03bcs"
    if ms < 1000.0:
        return f"{round(ms)}ms"
    total_ms = int(round(ms))
    seconds, rem_ms = divmod(total_ms, 1000)
    if seconds < 60:
        return f"{seconds}s {rem_ms}ms" if rem_ms else f"{seconds}s"
    minutes, rem_s = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {rem_s}s" if rem_s else f"{minutes}m"
    hours, rem_m = divmod(minutes, 60)
    return f"{hours}h {rem_m}m" if rem_m else f"{hours}h"

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
            self.mode = 1  # 0: GHOST, 1: LIVE
            self.mode_labels = [" Mode: GHOST RUN", " Mode: LIVE RUN"]
            self.center_x = 200
            self.y_cursor = 60 # Set to match SYSCALL: REFRESH default
            self.scroll_speed = 15.0
            self.last_sig = None
            self.last_sig_time = 0
            self.animator = FlowAnimator(center_x=200)
            dpg.create_context()
            dpg.create_viewport(
                title='DebugFlow', 
                width=WIN_WIDTH, 
                height=WIN_HEIGHT,
                x_pos=1500,
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

                    # Read until the bridge closes its end. The bridge always
                    # opens one socket per pulse and closes it after sendall,
                    # so EOF marks the end of one payload. This avoids the
                    # 1024-byte truncation that would silently drop pulses
                    # carrying long file paths + metadata.
                    chunks = []
                    conn.settimeout(0.25)
                    try:
                        while True:
                            chunk = conn.recv(4096)
                            if not chunk:
                                break
                            chunks.append(chunk)
                    except socket.timeout:
                        pass
                    finally:
                        try:
                            conn.close()
                        except Exception:
                            pass

                    if not chunks:
                        continue

                    decoded = b"".join(chunks).decode('utf-8', errors='replace')

                    try:
                        payload = json.loads(decoded)
                    except json.JSONDecodeError as je:
                        log.error(f"[SOCKET] bad JSON ({len(decoded)}B) from {addr}: {je}")
                        continue

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
            total_height = len(self.node_map) * NODE_SPACING
            max_scroll_limit = min(0, -(total_height - (WIN_HEIGHT / 2)))
            self.target_scroll = max(max_scroll_limit, min(0, self.target_scroll))
            self.scroll_offset += (self.target_scroll - self.scroll_offset) * scroll_speed
                
        except Exception as e:
            log.error(f"Scroll Manager Error: {e}")

    def _apply_styles(self):
        with dpg.theme() as global_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_style(dpg.mvStyleVar_ScrollbarSize, 0)
                dpg.add_theme_style(dpg.mvStyleVar_WindowBorderSize, 0)
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (7, 7, 9, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (0, 0, 0, 0))
        dpg.bind_theme(global_theme)

    def _setup_handlers(self):
        with dpg.handler_registry():
            dpg.add_mouse_drag_handler(button=0, callback=self._drag_callback)
            dpg.add_mouse_wheel_handler(callback=self._wheel_callback)
            dpg.add_mouse_click_handler(button=0, callback=self._click_callback)

    def _click_callback(self, sender, app_data):
        """
        Hit-test the click against each node's tight card area:
          orb  (circle at nx, ny_off, radius ~10)  +  name label to the right.

        Geometry used (mirrors _draw_node in the render loop):
          orb centre  : (nx, ny_off)
          name text   : starts at (nx + 20, ny_off - 9), ~8 px/glyph

        The hit zone is intentionally tight — just the orb width and the
        name label — so clicks in blank vertical space between nodes or on
        the params/returns text lines do NOT accidentally trigger a jump.

        DragHandle clicks are ignored so window-drag still works.
        """
        try:
            if dpg.is_item_hovered("DragHandle"):
                return
            mx, my = dpg.get_mouse_pos(local=True)

            # The NerveCanvas sits below the 40px DragHandle.
            # DPG drawlist coordinates are relative to the canvas origin,
            # but get_mouse_pos(local=True) is relative to PrimaryWindow.
            # Subtract the DragHandle offset so y-coordinates align.
            canvas_my = my - 40

            for node in self.node_map:
                nx, ny = node["pos"]
                ny_off = ny + self.scroll_offset

                # Quick cull — skip nodes fully outside the visible canvas.
                if not (-20 < ny_off < WIN_HEIGHT + 20):
                    continue

                # --- Tight rectangular hit-zone ---
                # Left  : left edge of the orb glow
                # Right : right edge of the name text only (not returns,
                #         which can be much wider and causes false hits)
                # Top   : just above orb centre
                # Bottom: just below orb centre (excludes returns line)
                name_w = len(node.get("name") or "") * 8
                left   = nx - 14
                right  = nx + 20 + name_w
                top    = ny_off - 12
                bottom = ny_off + 14

                if left <= mx <= right and top <= canvas_my <= bottom:
                    f  = node.get("file")
                    ln = node.get("line")
                    if f:
                        threading.Thread(
                            target=open_in_editor, args=(f, ln), daemon=True
                        ).start()
                    return
        except Exception as e:
            log.error(f"Click handler error: {e}")

    def _wheel_callback(self, sender, app_data):
        self.target_scroll += app_data * 40
        if self.target_scroll > 0: self.target_scroll = 0

    def _drag_callback(self, sender, app_data):
        if dpg.is_item_hovered("DragHandle"):
            pos = dpg.get_viewport_pos()
            dpg.set_viewport_pos([pos[0] + app_data[1], pos[1] + app_data[2]])

    def _format_type(self, val):
        if val is None or val == {}: 
            return "void"
        if isinstance(val, dict):
            return ", ".join([str(v) for v in val.values()])[:30]
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
                    new_params  = self._format_type(msg.get('params'))
                    new_returns = self._format_type(msg.get('returns'))
                    
                    if new_params  != "void": existing_node["params"]  = new_params
                    if new_returns != "void": existing_node["returns"] = new_returns

                    if msg.get("file") is not None:
                        existing_node["file"] = msg.get("file")
                        existing_node["line"] = msg.get("line")
                    if msg.get("module") is not None:
                        existing_node["module"] = msg.get("module")
                    if msg.get("duration_ms") is not None:
                        existing_node["duration_ms"] = msg.get("duration_ms")

                    prev_type = existing_node["type"]

                    # --- FIRE RETURN PULSE (UP) ---
                    # When a node that was processing transitions to done.
                    if prev_type == "processing" and msg_type in ["success", "nuke"]:
                        idx = self.node_map.index(existing_node)
                        if idx > 0:
                            self.animator.add_pulse(
                                start_y=existing_node["pos"][1], 
                                end_y=self.node_map[idx - 1]["pos"][1], 
                                p_type="up"
                            )

                    # --- FIRE REFRACTED CALL PULSE ---
                    # When a node that is NOT currently processing gets a fresh
                    # "processing" event — this is a cross-call or re-entry
                    # (e.g. function 5 calls function 1 again).  Fire a call
                    # pulse from the deepest currently-active node down/up to
                    # this one so the spine shows the jump.
                    if msg_type == "processing" and prev_type != "processing":
                        # Find the deepest node in the map that is actively
                        # processing (the caller).  Fall back to the last node.
                        caller_node = None
                        for n in reversed(self.node_map):
                            if n is not existing_node and n["type"] == "processing":
                                caller_node = n
                                break
                        if caller_node is None and self.node_map[-1] is not existing_node:
                            caller_node = self.node_map[-1]
                        if caller_node is not None:
                            self.animator.add_pulse(
                                start_y=caller_node["pos"][1],
                                end_y=existing_node["pos"][1],
                                p_type="down",
                            )

                    existing_node["type"] = msg_type
                    existing_node["current_alpha"] = 1.2 
                    continue

                # --- 3. NODE CREATION ---
                new_node = {
                    "pos":     [self.center_x, self.y_cursor],
                    "name":    node_raw_name,
                    "params":  self._format_type(msg.get('params')),
                    "returns": self._format_type(msg.get('returns')),
                    "type":    msg_type,
                    "birth":   time.time(),
                    "current_alpha": 1.0,
                    "file":    msg.get("file"),
                    "line":    msg.get("line"),
                    "module":  msg.get("module"),
                    "duration_ms": msg.get("duration_ms"),
                }
                
                # --- FIRE CALL PULSE (DOWN) ---
                if self.node_map:
                    self.animator.add_pulse(
                        start_y=self.node_map[-1]["pos"][1], 
                        end_y=self.y_cursor, 
                        p_type="down"
                    )

                self.node_map.append(new_node)
                
                # --- STICKY CENTER SCROLL ---
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
                        try:
                            os.remove(kill_signal)
                        except Exception:
                            pass
                        sys.exit(0)

                    # --- SMOOTH SCROLL ---
                    self.scroll_offset += (self.target_scroll - self.scroll_offset) * scroll_speed
                    
                    # Prevent scrolling past the last node
                    self.target_scroll = max(min(0, -(self.y_cursor - NODE_SPACING - 425)), self.target_scroll)
                    
                    m_pos = dpg.get_mouse_pos(local=True)
                    curr_time = time.time()

                    # 1. TRIGGER ANIMATOR (Spine, Pulses, Ripples)
                    node_pts    = [n["pos"] for n in self.node_map]
                    node_states = [n.get("type", "ghost") for n in self.node_map]
                    self.animator.update_and_draw(
                        "NerveCanvas", self.scroll_offset, node_pts, node_states
                    )

                    # 2. DRAW HUD OVERLAYS (Labels & State Orbs)
                    for node in self.node_map:
                        nx, ny = node["pos"]
                        ny_off = ny + self.scroll_offset
                        
                        # Culling: Only process if visible
                        if not (-100 < ny_off < WIN_HEIGHT + 100): continue

                        # Alpha & Hover Logic
                        dist = math.sqrt((m_pos[0] - nx)**2 + ((m_pos[1] - 40) - ny_off)**2)
                        target_a = 1.0 if node.get("type") == "processing" or dist < 45 else 0.7
                        node["current_alpha"] += (target_a - node["current_alpha"]) * 0.1
                        m_alpha = int(node["current_alpha"] * 255)
                        
                        # State Color Mapping
                        node_type = node.get("type", "ghost")
                        if node_type == "nuke":
                            orb_c       = [255,  50,  50]
                            text_accent = [255, 100, 100, m_alpha]
                        elif node_type == "processing":
                            orb_c       = [255, 200,   0]
                            text_accent = [255, 220, 100, m_alpha]
                        elif node_type == "success":
                            orb_c       = [  0, 255, 150]
                            text_accent = [  0, 200, 255, m_alpha]
                        else:
                            orb_c       = [100, 100, 100]
                            text_accent = [  0, 200, 255, m_alpha]

                        # Params (TOP)
                        dpg.draw_text(
                            pos=[nx - (len(node["params"]) * 3.8), ny_off - 28], 
                            text=node["params"],
                            color=text_accent, size=14, parent="NerveCanvas"
                        )
                        # Name (CENTER-RIGHT)
                        dpg.draw_text(
                            pos=[nx + 20, ny_off - 9], 
                            text=node["name"],
                            color=[255, 255, 255, m_alpha], size=16, parent="NerveCanvas"
                        )
                        # Returns (BOTTOM)
                        dpg.draw_text(
                            pos=[nx - (len(node["returns"]) * 3.8), ny_off + 14], 
                            text=node["returns"],
                            color=text_accent, size=14, parent="NerveCanvas"
                        )

                        # --- HOVER METADATA OVERLAY ---
                        if dist < 45:
                            mod = node.get("module") or ""
                            dur = _format_duration(node.get("duration_ms"))
                            parts = [p for p in (mod, dur) if p]
                            if parts:
                                meta_text = " · ".join(parts)
                                dpg.draw_text(
                                    pos=[nx - (len(meta_text) * 3.8), ny_off + 34],
                                    text=meta_text,
                                    color=[120, 200, 230, m_alpha],
                                    size=14,
                                    parent="NerveCanvas",
                                )

                        # State Core — drawn AFTER the animator's beat core so the
                        # correct state colour is always on top.
                        dpg.draw_circle(
                            center=[nx, ny_off], radius=5,
                            color=[*orb_c, 255], fill=[*orb_c, 255],
                            parent="NerveCanvas"
                        )

                    dpg.render_dearpygui_frame()
                except Exception as e:
                    log.error(f"Frame Error: {e}")
                    time.sleep(0.01)
        finally:
            dpg.destroy_context()

            
if __name__ == "__main__":
    q = queue.Queue()
    FlowHUD(q).run()
