"""
Build the README demo GIFs.

Why this script exists
----------------------
The real DebugFlow HUD is Dear PyGui rendered on a desktop OS — it cannot be
recorded inside a headless Replit/Linux container (no display server, no GPU
context). This script renders frames in Pillow that match the HUD's
production palette, geometry, and animation curves so the README has a
faithful preview of what users will see on their own machine.

The visual contract is taken straight from the HUD source:
    * cyan/teal palette (call pulse, idle node)
    * yellow during processing
    * green on success, red on exception
    * vertical "spine" line, ~110px node spacing
    * downward pulse on call, upward on return
    * smooth-scroll once 5+ nodes spawn

Outputs
-------
    images/demo_pulse.gif    — call flow: nodes spawn and pulse down the spine
    images/demo_returns.gif  — return flow: green success / red exception
    images/demo_hover.gif    — hover overlay: module + duration metadata

Run:
    python scripts/build_demo_gifs.py
"""
import math
import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# --- Palette (matches flow_hud.py) ------------------------------------------
BG          = (10, 14, 20, 255)       # near-black with a hint of blue
SPINE       = (40, 110, 130, 255)     # dim teal
NODE_IDLE   = (90, 200, 230, 255)     # cyan
NODE_PROC   = (240, 200, 60, 255)     # yellow
NODE_OK     = (80, 220, 130, 255)     # green
NODE_ERR    = (235, 80, 80, 255)      # red
TEXT_LABEL  = (200, 230, 240, 255)    # near-white cyan
TEXT_DIM    = (120, 200, 230, 255)    # hover metadata color
TEXT_PARAM  = (90, 200, 230, 255)     # cyan for param text

# --- Geometry ---------------------------------------------------------------
W, H        = 480, 600
SPINE_X     = 175           # vertical spine at this x
NODE_R      = 11
NODE_GAP    = 100           # vertical spacing between nodes (matches HUD ratio)


# --- Font helper ------------------------------------------------------------
def _load_font(size):
    """Try to find a monospace TTF; fall back to PIL default if none.

    NOTE: do NOT glob `/nix/store/*` here — that walks the entire Nix store
    on Replit and stalls the script for minutes. Stick to fixed paths.
    """
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
        "/System/Library/Fonts/Menlo.ttc",
        "C:/Windows/Fonts/consola.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


FONT_LABEL = _load_font(15)
FONT_PARAM = _load_font(11)
FONT_HEAD  = _load_font(13)
FONT_HOVER = _load_font(11)


# ---------------------------------------------------------------------------
# Drawing primitives
# ---------------------------------------------------------------------------
def _new_frame():
    img = Image.new("RGBA", (W, H), BG)
    return img, ImageDraw.Draw(img, "RGBA")


def _glow_circle(draw, cx, cy, r, color, glow_alpha=70):
    """Filled circle with a soft outer glow halo."""
    # Outer halo
    halo = (color[0], color[1], color[2], glow_alpha)
    for k in (3, 2, 1):
        draw.ellipse(
            (cx - r - k * 3, cy - r - k * 3, cx + r + k * 3, cy + r + k * 3),
            outline=halo, width=1,
        )
    # Solid core
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
    # Inner highlight
    hi = (255, 255, 255, 180)
    draw.ellipse((cx - r + 3, cy - r + 3, cx - 1, cy - 1), fill=hi)


def _draw_spine(draw, top_y, bot_y):
    draw.line((SPINE_X, top_y, SPINE_X, bot_y), fill=SPINE, width=2)


def _draw_header(draw, text):
    draw.text((SPINE_X - 65, 14), text, fill=TEXT_DIM, font=FONT_HEAD)


def _draw_node(draw, cx, cy, color, label, params=None, returns=None):
    # param text above node (typed)
    if params is not None:
        draw.text((cx + 22, cy - 28), params, fill=TEXT_PARAM, font=FONT_PARAM)
    _glow_circle(draw, cx, cy, NODE_R, color)
    draw.text((cx + 22, cy - 8), label, fill=TEXT_LABEL, font=FONT_LABEL)
    if returns is not None:
        draw.text((cx + 22, cy + 14), returns, fill=TEXT_PARAM, font=FONT_PARAM)


def _draw_pulse(draw, cx, y, direction="down", alpha=255):
    """A small travelling spark on the spine."""
    color = (140, 230, 250, alpha)
    if direction == "down":
        draw.polygon(
            [(cx, y + 6), (cx - 4, y - 4), (cx + 4, y - 4)], fill=color,
        )
    else:
        draw.polygon(
            [(cx, y - 6), (cx - 4, y + 4), (cx + 4, y + 4)], fill=color,
        )


# ---------------------------------------------------------------------------
# Demo 1 — call flow: nodes spawn one after another, downward pulse
# ---------------------------------------------------------------------------
NODES = [
    ("load_dataset",   "path: str=\"data.csv\"",         "DataFrame[1000, 8]", NODE_OK),
    ("preprocess",     "df: DataFrame[1000, 8]",         "DataFrame[1000, 12]", NODE_OK),
    ("train_model",    "X: ndarray[1000, 12]",           "Model<acc=0.94>",     NODE_OK),
    ("save_checkpoint","model: Model, epoch: int=3",     "ok",                  NODE_OK),
]


def build_demo_pulse(out_path):
    frames = []
    fps = 18
    seconds_per_node = 0.9
    pulse_steps = int(fps * seconds_per_node)

    visible = []  # nodes already spawned
    for i, (label, params, returns, final_color) in enumerate(NODES):
        node_y = 90 + i * NODE_GAP

        # Phase A: pulse travels from previous node (or top) → new node
        start_y = 60 if i == 0 else (90 + (i - 1) * NODE_GAP) + NODE_R + 4
        for step in range(pulse_steps):
            t = step / pulse_steps
            img, draw = _new_frame()
            _draw_header(draw, "Mode: LIVE TRACE")
            _draw_spine(draw, 60, 90 + (len(NODES) - 1) * NODE_GAP + 30)

            # already-finished nodes
            for (lbl, prm, ret, col), y in visible:
                _draw_node(draw, SPINE_X, y, col, lbl, prm, ret)

            # downward pulse mid-flight
            spark_y = start_y + (node_y - NODE_R - 4 - start_y) * t
            _draw_pulse(draw, SPINE_X, spark_y, "down")

            # the new node appears partway through, in PROCESSING (yellow)
            if t > 0.55:
                fade = min(1.0, (t - 0.55) / 0.4)
                proc_color = (
                    int(NODE_PROC[0] * fade + BG[0] * (1 - fade)),
                    int(NODE_PROC[1] * fade + BG[1] * (1 - fade)),
                    int(NODE_PROC[2] * fade + BG[2] * (1 - fade)),
                    255,
                )
                _draw_node(draw, SPINE_X, node_y, proc_color, label, params, None)

            frames.append(img.convert("P", palette=Image.Palette.ADAPTIVE))

        # Phase B: short hold on the processing node (yellow)
        for _ in range(int(fps * 0.25)):
            img, draw = _new_frame()
            _draw_header(draw, "Mode: LIVE TRACE")
            _draw_spine(draw, 60, 90 + (len(NODES) - 1) * NODE_GAP + 30)
            for (lbl, prm, ret, col), y in visible:
                _draw_node(draw, SPINE_X, y, col, lbl, prm, ret)
            # heartbeat: subtle radius wiggle
            r_wiggle = NODE_R + int(2 * math.sin(_ * 1.2))
            _glow_circle(draw, SPINE_X, node_y, r_wiggle, NODE_PROC)
            draw.text((SPINE_X + 22, node_y - 28), params, fill=TEXT_PARAM, font=FONT_PARAM)
            draw.text((SPINE_X + 22, node_y - 8), label, fill=TEXT_LABEL, font=FONT_LABEL)
            frames.append(img.convert("P", palette=Image.Palette.ADAPTIVE))

        # Phase C: settle — node turns final color, return value appears
        visible.append(((label, params, returns, final_color), node_y))

    # Final hold so the loop has a clear terminator
    for _ in range(int(fps * 1.4)):
        img, draw = _new_frame()
        _draw_header(draw, "Mode: LIVE TRACE  ·  done")
        _draw_spine(draw, 60, 90 + (len(NODES) - 1) * NODE_GAP + 30)
        for (lbl, prm, ret, col), y in visible:
            _draw_node(draw, SPINE_X, y, col, lbl, prm, ret)
        frames.append(img.convert("P", palette=Image.Palette.ADAPTIVE))

    frames[0].save(
        out_path, save_all=True, append_images=frames[1:],
        duration=int(1000 / fps), loop=0, disposal=2,
    )
    print(f"  ✔  {out_path}  ({len(frames)} frames)")


# ---------------------------------------------------------------------------
# Demo 2 — return flow: green success vs red exception
# ---------------------------------------------------------------------------
RETURN_NODES = [
    ("validate_input", "x: int=42",           "True",                  NODE_OK),
    ("connect_db",     "host: str=\"prod\"",  "Connection<#7>",        NODE_OK),
    ("query",          "sql: str=\"SELECT…\"", "Rows[128]",             NODE_OK),
    ("commit",         "tx: Transaction",      "RAISED: IntegrityError", NODE_ERR),
]


def build_demo_returns(out_path):
    frames = []
    fps = 18

    # Spawn all nodes immediately as PROCESSING
    base_state = []
    for i, (label, params, returns, color) in enumerate(RETURN_NODES):
        node_y = 90 + i * NODE_GAP
        base_state.append({
            "label": label, "params": params,
            "returns": returns, "final": color, "y": node_y,
            "current": NODE_PROC,
        })

    # Brief hold while all are yellow
    for _ in range(int(fps * 0.6)):
        img, draw = _new_frame()
        _draw_header(draw, "Mode: LIVE TRACE")
        _draw_spine(draw, 60, 90 + (len(RETURN_NODES) - 1) * NODE_GAP + 30)
        for n in base_state:
            _draw_node(draw, SPINE_X, n["y"], n["current"], n["label"], n["params"], None)
        frames.append(img.convert("P", palette=Image.Palette.ADAPTIVE))

    # Walk bottom-up, firing return pulses
    for i in reversed(range(len(RETURN_NODES))):
        n = base_state[i]
        target_color = n["final"]
        flash_steps = int(fps * 0.45)

        # Pulse travels UPWARD from this node to the previous one (or top)
        start_y = n["y"] - NODE_R - 4
        end_y = 60 if i == 0 else base_state[i - 1]["y"] + NODE_R + 4
        travel_steps = int(fps * 0.55)
        for step in range(travel_steps):
            t = step / travel_steps
            img, draw = _new_frame()
            _draw_header(draw, "Mode: LIVE TRACE")
            _draw_spine(draw, 60, 90 + (len(RETURN_NODES) - 1) * NODE_GAP + 30)
            for nn in base_state:
                _draw_node(draw, SPINE_X, nn["y"], nn["current"], nn["label"], nn["params"],
                           nn["returns"] if nn["current"] is not NODE_PROC else None)
            spark_y = start_y + (end_y - start_y) * t
            _draw_pulse(draw, SPINE_X, spark_y, "up")
            # also flash the source node's color in time with the pulse
            flash_t = min(1.0, t * 1.6)
            blended = (
                int(NODE_PROC[0] * (1 - flash_t) + target_color[0] * flash_t),
                int(NODE_PROC[1] * (1 - flash_t) + target_color[1] * flash_t),
                int(NODE_PROC[2] * (1 - flash_t) + target_color[2] * flash_t),
                255,
            )
            _glow_circle(draw, SPINE_X, n["y"], NODE_R, blended)
            draw.text((SPINE_X + 22, n["y"] - 28), n["params"], fill=TEXT_PARAM, font=FONT_PARAM)
            draw.text((SPINE_X + 22, n["y"] - 8), n["label"], fill=TEXT_LABEL, font=FONT_LABEL)
            frames.append(img.convert("P", palette=Image.Palette.ADAPTIVE))

        # Settle this node into its final color + return text
        n["current"] = target_color
        for _ in range(int(fps * 0.2)):
            img, draw = _new_frame()
            _draw_header(draw, "Mode: LIVE TRACE")
            _draw_spine(draw, 60, 90 + (len(RETURN_NODES) - 1) * NODE_GAP + 30)
            for nn in base_state:
                _draw_node(draw, SPINE_X, nn["y"], nn["current"], nn["label"], nn["params"],
                           nn["returns"] if nn["current"] is not NODE_PROC else None)
            frames.append(img.convert("P", palette=Image.Palette.ADAPTIVE))

    # Final hold
    for _ in range(int(fps * 1.6)):
        img, draw = _new_frame()
        _draw_header(draw, "Mode: LIVE TRACE  ·  exception in commit()")
        _draw_spine(draw, 60, 90 + (len(RETURN_NODES) - 1) * NODE_GAP + 30)
        for nn in base_state:
            _draw_node(draw, SPINE_X, nn["y"], nn["current"], nn["label"], nn["params"], nn["returns"])
        frames.append(img.convert("P", palette=Image.Palette.ADAPTIVE))

    frames[0].save(
        out_path, save_all=True, append_images=frames[1:],
        duration=int(1000 / fps), loop=0,  disposal=2,
    )
    print(f"  ✔  {out_path}  ({len(frames)} frames)")


# ---------------------------------------------------------------------------
# Demo 3 — hover metadata overlay
# ---------------------------------------------------------------------------
HOVER_NODES = [
    ("load_batch",     "idx: int=0",                "Tensor[64, 256]",    NODE_OK,  "pipeline · 880us"),
    ("forward",        "batch: Tensor[64, 256]",    "Tensor[64]",         NODE_OK,  "model · 12.4ms"),
    ("training_step",  "idx: int=0",                "float=0.241",        NODE_OK,  "trainer · 14.1ms"),
]


def build_demo_hover(out_path):
    """
    Hover demo. To stop PIL's gif encoder from collapsing visually-identical
    frames into a 5-frame stub (which then plays at fps speed and looks broken)
    we run a per-frame heartbeat on the hovered node + stamp an invisible
    counter pixel at (0,0) so the encoded byte stream of every frame differs.
    """
    fps = 18

    def _draw(hover_idx, phase):
        img, draw = _new_frame()
        _draw_header(draw, "Mode: LIVE TRACE  ·  hover for metadata")
        _draw_spine(draw, 60, 90 + (len(HOVER_NODES) - 1) * NODE_GAP + 30)
        for i, (lbl, prm, ret, col, meta) in enumerate(HOVER_NODES):
            y = 90 + i * NODE_GAP
            shown_col = col
            r = NODE_R
            if hover_idx is not None and i != hover_idx:
                shown_col = (col[0] // 2 + 20, col[1] // 2 + 20, col[2] // 2 + 20, 200)
            elif i == hover_idx:
                # ±1px heartbeat so each frame's pixels really differ
                r = NODE_R + (1 if math.sin(phase * 1.4) > 0 else 0)
            _draw_node(draw, SPINE_X, y, shown_col, lbl, prm, ret)
            if i == hover_idx:
                _glow_circle(draw, SPINE_X, y, r, col)
                draw.text((SPINE_X + 22, y + 30), meta,
                          fill=TEXT_DIM, font=FONT_HOVER)
        # Per-frame uniqueness pixel — invisible but ensures encoder keeps the frame.
        draw.point((0, 0), fill=(phase % 256, 0, 0, 255))
        return img.convert("P", palette=Image.Palette.ADAPTIVE)

    frames = []
    phase = 0
    for _ in range(int(fps * 0.7)):
        frames.append(_draw(None, phase)); phase += 1
    for idx in range(len(HOVER_NODES)):
        for _ in range(int(fps * 1.2)):
            frames.append(_draw(idx, phase)); phase += 1
    for _ in range(int(fps * 0.7)):
        frames.append(_draw(None, phase)); phase += 1

    frames[0].save(
        out_path, save_all=True, append_images=frames[1:],
        duration=int(1000 / fps), loop=0, disposal=2,
    )
    print(f"  ✔  {out_path}  ({len(frames)} frames)")


# ---------------------------------------------------------------------------
def main():
    out_dir = Path(__file__).resolve().parent.parent / "images"
    out_dir.mkdir(exist_ok=True)
    print(f"Building demo GIFs into {out_dir}")
    build_demo_pulse(out_dir / "demo_pulse.gif")
    build_demo_returns(out_dir / "demo_returns.gif")
    build_demo_hover(out_dir / "demo_hover.gif")
    print("Done.")


if __name__ == "__main__":
    main()
