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
    * purple for refracted (cross) calls going backward on the spine
    * vertical "spine" line, ~110px node spacing
    * downward pulse on call, upward on return
    * smooth-scroll once 5+ nodes spawn

Outputs (saved to demo_gifs/)
-------------------------------
    demo_pulse.gif          — call flow: nodes spawn and pulse down the spine
    demo_returns.gif        — return flow: green success / red exception
    demo_hover.gif          — hover overlay: module + duration metadata
    demo_refracted.gif      — f5 calls f1: pulse travels back UP the spine
    demo_crash_blame.gif    — A→B→C chain: C crashes, blame propagates upward
    demo_streaming.gif      — data stream: many rapid pulses travelling simultaneously
    demo_all_states.gif     — all four node states side-by-side

Run:
    python scripts/build_demo_gifs.py
"""
import math
import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# --- Palette (matches flow_hud.py + animation.py) ---------------------------
BG            = (10, 14, 20, 255)
SPINE         = (40, 110, 130, 255)
NODE_IDLE     = (90, 200, 230, 255)
NODE_PROC     = (240, 200, 60, 255)
NODE_OK       = (80, 220, 130, 255)
NODE_ERR      = (235, 80, 80, 255)
NODE_GHOST    = (100, 100, 110, 255)
TEXT_LABEL    = (200, 230, 240, 255)
TEXT_DIM      = (120, 200, 230, 255)
TEXT_PARAM    = (90, 200, 230, 255)
TEXT_ERR      = (255, 120, 120, 255)

# Pulse colours (match animation.py constants)
PULSE_DOWN       = (0, 255, 200, 255)    # teal — normal call
PULSE_UP         = (120, 200, 255, 255)  # sky-blue — return
PULSE_REFRACTED  = (180, 120, 255, 255)  # purple — call going back up

# --- Geometry ---------------------------------------------------------------
W, H      = 480, 600
SPINE_X   = 175
NODE_R    = 11
NODE_GAP  = 100


# --- Font helper ------------------------------------------------------------
def _load_font(size):
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
def _new_frame(w=W, h=H):
    img = Image.new("RGBA", (w, h), BG)
    return img, ImageDraw.Draw(img, "RGBA")


def _glow_circle(draw, cx, cy, r, color, glow_alpha=70):
    halo = (color[0], color[1], color[2], glow_alpha)
    for k in (3, 2, 1):
        draw.ellipse(
            (cx - r - k*3, cy - r - k*3, cx + r + k*3, cy + r + k*3),
            outline=halo, width=1,
        )
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
    hi = (255, 255, 255, 180)
    draw.ellipse((cx - r + 3, cy - r + 3, cx - 1, cy - 1), fill=hi)


def _draw_spine(draw, top_y, bot_y, cx=SPINE_X):
    draw.line((cx, top_y, cx, bot_y), fill=SPINE, width=2)


def _draw_header(draw, text):
    draw.text((SPINE_X - 65, 14), text, fill=TEXT_DIM, font=FONT_HEAD)


def _draw_node(draw, cx, cy, color, label, params=None, returns=None, err=False):
    param_col  = TEXT_ERR if err else TEXT_PARAM
    return_col = TEXT_ERR if err else TEXT_PARAM
    if params is not None:
        draw.text((cx + 22, cy - 28), params, fill=param_col, font=FONT_PARAM)
    _glow_circle(draw, cx, cy, NODE_R, color)
    draw.text((cx + 22, cy - 8), label, fill=TEXT_LABEL, font=FONT_LABEL)
    if returns is not None:
        draw.text((cx + 22, cy + 14), returns, fill=return_col, font=FONT_PARAM)


def _draw_pulse(draw, cx, y, color=PULSE_DOWN, alpha=255, direction="down"):
    c = (color[0], color[1], color[2], alpha)
    if direction == "down":
        draw.polygon([(cx, y+6), (cx-4, y-4), (cx+4, y-4)], fill=c)
    else:
        draw.polygon([(cx, y-6), (cx-4, y+4), (cx+4, y+4)], fill=c)


def _blend(a, b, t):
    return tuple(int(a[i]*(1-t) + b[i]*t) for i in range(4))


# ---------------------------------------------------------------------------
# Demo 1 — call flow
# ---------------------------------------------------------------------------
NODES = [
    ("load_dataset",    "path: str=\"data.csv\"",        "DataFrame[1000, 8]",  NODE_OK),
    ("preprocess",      "df: DataFrame[1000, 8]",        "DataFrame[1000, 12]", NODE_OK),
    ("train_model",     "X: ndarray[1000, 12]",          "Model<acc=0.94>",     NODE_OK),
    ("save_checkpoint", "model: Model, epoch: int=3",    "ok",                  NODE_OK),
]


def build_demo_pulse(out_path):
    frames = []
    fps = 18
    seconds_per_node = 0.9
    pulse_steps = int(fps * seconds_per_node)

    visible = []
    for i, (label, params, returns, final_color) in enumerate(NODES):
        node_y = 90 + i * NODE_GAP
        start_y = 60 if i == 0 else (90 + (i-1)*NODE_GAP) + NODE_R + 4

        for step in range(pulse_steps):
            t = step / pulse_steps
            img, draw = _new_frame()
            _draw_header(draw, "Mode: LIVE TRACE")
            _draw_spine(draw, 60, 90 + (len(NODES)-1)*NODE_GAP + 30)
            for (lbl, prm, ret, col), y in visible:
                _draw_node(draw, SPINE_X, y, col, lbl, prm, ret)
            spark_y = start_y + (node_y - NODE_R - 4 - start_y) * t
            _draw_pulse(draw, SPINE_X, spark_y, PULSE_DOWN, direction="down")
            if t > 0.55:
                fade = min(1.0, (t - 0.55) / 0.4)
                proc_color = (
                    int(NODE_PROC[0]*fade + BG[0]*(1-fade)),
                    int(NODE_PROC[1]*fade + BG[1]*(1-fade)),
                    int(NODE_PROC[2]*fade + BG[2]*(1-fade)),
                    255,
                )
                _draw_node(draw, SPINE_X, node_y, proc_color, label, params, None)
            frames.append(img.convert("P", palette=Image.ADAPTIVE))

        for _ in range(int(fps * 0.25)):
            img, draw = _new_frame()
            _draw_header(draw, "Mode: LIVE TRACE")
            _draw_spine(draw, 60, 90 + (len(NODES)-1)*NODE_GAP + 30)
            for (lbl, prm, ret, col), y in visible:
                _draw_node(draw, SPINE_X, y, col, lbl, prm, ret)
            r_wiggle = NODE_R + int(2 * math.sin(_ * 1.2))
            _glow_circle(draw, SPINE_X, node_y, r_wiggle, NODE_PROC)
            draw.text((SPINE_X + 22, node_y - 28), params, fill=TEXT_PARAM, font=FONT_PARAM)
            draw.text((SPINE_X + 22, node_y - 8), label, fill=TEXT_LABEL, font=FONT_LABEL)
            frames.append(img.convert("P", palette=Image.ADAPTIVE))

        visible.append(((label, params, returns, final_color), node_y))

    for _ in range(int(fps * 1.4)):
        img, draw = _new_frame()
        _draw_header(draw, "Mode: LIVE TRACE  ·  done")
        _draw_spine(draw, 60, 90 + (len(NODES)-1)*NODE_GAP + 30)
        for (lbl, prm, ret, col), y in visible:
            _draw_node(draw, SPINE_X, y, col, lbl, prm, ret)
        frames.append(img.convert("P", palette=Image.ADAPTIVE))

    frames[0].save(out_path, save_all=True, append_images=frames[1:],
                   duration=int(1000/fps), loop=0, disposal=2)
    print(f"  ✔  {out_path}  ({len(frames)} frames)")


# ---------------------------------------------------------------------------
# Demo 2 — return flow
# ---------------------------------------------------------------------------
RETURN_NODES = [
    ("validate_input", "x: int=42",             "True",                   NODE_OK),
    ("connect_db",     "host: str=\"prod\"",     "Connection<#7>",         NODE_OK),
    ("query",          "sql: str=\"SELECT…\"",   "Rows[128]",              NODE_OK),
    ("commit",         "tx: Transaction",        "RAISED: IntegrityError", NODE_ERR),
]


def build_demo_returns(out_path):
    frames = []
    fps = 18

    base_state = []
    for i, (label, params, returns, color) in enumerate(RETURN_NODES):
        node_y = 90 + i * NODE_GAP
        base_state.append({
            "label": label, "params": params,
            "returns": returns, "final": color, "y": node_y,
            "current": NODE_PROC,
        })

    for _ in range(int(fps * 0.6)):
        img, draw = _new_frame()
        _draw_header(draw, "Mode: LIVE TRACE")
        _draw_spine(draw, 60, 90 + (len(RETURN_NODES)-1)*NODE_GAP + 30)
        for n in base_state:
            _draw_node(draw, SPINE_X, n["y"], n["current"], n["label"], n["params"], None)
        frames.append(img.convert("P", palette=Image.ADAPTIVE))

    for i in reversed(range(len(RETURN_NODES))):
        n = base_state[i]
        target_color = n["final"]
        start_y = n["y"] - NODE_R - 4
        end_y   = 60 if i == 0 else base_state[i-1]["y"] + NODE_R + 4
        travel_steps = int(fps * 0.55)
        for step in range(travel_steps):
            t = step / travel_steps
            img, draw = _new_frame()
            _draw_header(draw, "Mode: LIVE TRACE")
            _draw_spine(draw, 60, 90 + (len(RETURN_NODES)-1)*NODE_GAP + 30)
            for nn in base_state:
                _draw_node(draw, SPINE_X, nn["y"], nn["current"], nn["label"], nn["params"],
                           nn["returns"] if nn["current"] is not NODE_PROC else None,
                           err=(nn["final"] is NODE_ERR and nn["current"] is not NODE_PROC))
            spark_y = start_y + (end_y - start_y) * t
            _draw_pulse(draw, SPINE_X, spark_y, PULSE_UP, direction="up")
            flash_t = min(1.0, t * 1.6)
            blended = _blend(NODE_PROC + (255,), target_color + (255,), flash_t)
            _glow_circle(draw, SPINE_X, n["y"], NODE_R, blended[:3] + (255,))
            draw.text((SPINE_X + 22, n["y"] - 28), n["params"], fill=TEXT_PARAM, font=FONT_PARAM)
            draw.text((SPINE_X + 22, n["y"] - 8), n["label"], fill=TEXT_LABEL, font=FONT_LABEL)
            frames.append(img.convert("P", palette=Image.ADAPTIVE))

        n["current"] = target_color
        for _ in range(int(fps * 0.2)):
            img, draw = _new_frame()
            _draw_header(draw, "Mode: LIVE TRACE")
            _draw_spine(draw, 60, 90 + (len(RETURN_NODES)-1)*NODE_GAP + 30)
            for nn in base_state:
                _draw_node(draw, SPINE_X, nn["y"], nn["current"], nn["label"], nn["params"],
                           nn["returns"] if nn["current"] is not NODE_PROC else None,
                           err=(nn["final"] is NODE_ERR and nn["current"] is not NODE_PROC))
            frames.append(img.convert("P", palette=Image.ADAPTIVE))

    for _ in range(int(fps * 1.6)):
        img, draw = _new_frame()
        _draw_header(draw, "Mode: LIVE TRACE  ·  exception in commit()")
        _draw_spine(draw, 60, 90 + (len(RETURN_NODES)-1)*NODE_GAP + 30)
        for nn in base_state:
            _draw_node(draw, SPINE_X, nn["y"], nn["current"], nn["label"], nn["params"],
                       nn["returns"],
                       err=(nn["final"] is NODE_ERR))
        frames.append(img.convert("P", palette=Image.ADAPTIVE))

    frames[0].save(out_path, save_all=True, append_images=frames[1:],
                   duration=int(1000/fps), loop=0, disposal=2)
    print(f"  ✔  {out_path}  ({len(frames)} frames)")


# ---------------------------------------------------------------------------
# Demo 3 — hover metadata
# ---------------------------------------------------------------------------
HOVER_NODES = [
    ("load_batch",    "idx: int=0",              "Tensor[64, 256]",   NODE_OK, "pipeline · 880us"),
    ("forward",       "batch: Tensor[64, 256]",  "Tensor[64]",        NODE_OK, "model · 12.4ms"),
    ("training_step", "idx: int=0",              "float=0.241",       NODE_OK, "trainer · 14.1ms"),
]


def build_demo_hover(out_path):
    fps = 18

    def _draw(hover_idx, phase):
        img, draw = _new_frame()
        _draw_header(draw, "Mode: LIVE TRACE  ·  hover for metadata")
        _draw_spine(draw, 60, 90 + (len(HOVER_NODES)-1)*NODE_GAP + 30)
        for i, (lbl, prm, ret, col, meta) in enumerate(HOVER_NODES):
            y = 90 + i * NODE_GAP
            shown_col = col
            r = NODE_R
            if hover_idx is not None and i != hover_idx:
                shown_col = (col[0]//2+20, col[1]//2+20, col[2]//2+20, 200)
            elif i == hover_idx:
                r = NODE_R + (1 if math.sin(phase * 1.4) > 0 else 0)
            _draw_node(draw, SPINE_X, y, shown_col, lbl, prm, ret)
            if i == hover_idx:
                _glow_circle(draw, SPINE_X, y, r, col)
                draw.text((SPINE_X + 22, y + 30), meta, fill=TEXT_DIM, font=FONT_HOVER)
        draw.point((0, 0), fill=(phase % 256, 0, 0, 255))
        return img.convert("P", palette=Image.ADAPTIVE)

    frames = []
    phase = 0
    for _ in range(int(fps * 0.7)):
        frames.append(_draw(None, phase)); phase += 1
    for idx in range(len(HOVER_NODES)):
        for _ in range(int(fps * 1.2)):
            frames.append(_draw(idx, phase)); phase += 1
    for _ in range(int(fps * 0.7)):
        frames.append(_draw(None, phase)); phase += 1

    frames[0].save(out_path, save_all=True, append_images=frames[1:],
                   duration=int(1000/fps), loop=0, disposal=2)
    print(f"  ✔  {out_path}  ({len(frames)} frames)")


# ---------------------------------------------------------------------------
# Demo 4 — refracted (cross) call: function 5 calls function 1
#
# The spine shows five nodes top-to-bottom.  After all settle, a purple
# "refracted" pulse fires from f5 (bottom) back UP to f1 (top), then f1
# flashes yellow (re-processing) before turning green again.
# ---------------------------------------------------------------------------
REFRACTED_NODES = [
    ("initialise",   "cfg: dict",          "Config",     NODE_OK),
    ("load_data",    "cfg: Config",        "Dataset",    NODE_OK),
    ("augment",      "ds: Dataset",        "Dataset",    NODE_OK),
    ("train_epoch",  "ds: Dataset",        "float=0.87", NODE_OK),
    ("loop_ctrl",    "epoch: int=3",       "None",       NODE_OK),
]


def build_demo_refracted(out_path):
    fps = 18
    frames = []

    node_ys = [90 + i * NODE_GAP for i in range(len(REFRACTED_NODES))]
    spine_bot = node_ys[-1] + 30

    def _base_frame(header, active_idx=None, active_col=None):
        img, draw = _new_frame()
        _draw_header(draw, header)
        _draw_spine(draw, 60, spine_bot)
        for i, (lbl, prm, ret, col) in enumerate(REFRACTED_NODES):
            c = active_col if (i == active_idx and active_col) else col
            _draw_node(draw, SPINE_X, node_ys[i], c, lbl, prm, ret)
        return img, draw

    # Phase A: spawn nodes one by one (condensed — 8 frames each)
    spawn_frames = 8
    visible = []
    for i, (label, params, returns, color) in enumerate(REFRACTED_NODES):
        for step in range(spawn_frames):
            t = step / spawn_frames
            img, draw = _new_frame()
            _draw_header(draw, "Mode: LIVE TRACE")
            _draw_spine(draw, 60, spine_bot)
            for (lbl, prm, ret, col), y in visible:
                _draw_node(draw, SPINE_X, y, col, lbl, prm, ret)
            if t > 0.4:
                fade = min(1.0, (t - 0.4) / 0.5)
                pc = tuple(int(NODE_PROC[j]*fade + BG[j]*(1-fade)) for j in range(3)) + (255,)
                _draw_node(draw, SPINE_X, node_ys[i], pc, label, params, None)
            frames.append(img.convert("P", palette=Image.ADAPTIVE))
        visible.append(((label, params, returns, color), node_ys[i]))

    # Phase B: short hold
    for _ in range(int(fps * 0.5)):
        img, draw = _base_frame("Mode: LIVE TRACE  ·  all settled")
        frames.append(img.convert("P", palette=Image.ADAPTIVE))

    # Phase C: refracted call — f5 (loop_ctrl) calls f1 (initialise) again
    #   Purple pulse travels from y=node_ys[4] UP to y=node_ys[0]
    travel_steps = int(fps * 0.9)
    start_y = node_ys[4]
    end_y   = node_ys[0]
    for step in range(travel_steps):
        t = step / travel_steps
        img, draw = _base_frame("loop_ctrl → initialise  (cross-call ↑)")
        spark_y = start_y + (end_y - start_y) * t
        # Draw comet tail (3 segments)
        for k in range(3):
            t2 = t - k * 0.05
            if t2 < 0: continue
            sy = start_y + (end_y - start_y) * t2
            alpha = int(180 * (1 - k/3))
            draw.ellipse((SPINE_X-2, sy-2, SPINE_X+2, sy+2),
                         fill=(*PULSE_REFRACTED[:3], alpha))
        draw.ellipse((SPINE_X-4, spark_y-4, SPINE_X+4, spark_y+4),
                     fill=PULSE_REFRACTED)
        frames.append(img.convert("P", palette=Image.ADAPTIVE))

    # Phase D: f1 (initialise) re-enters processing then resolves
    for step in range(int(fps * 0.4)):
        beat = math.sin(step * 0.8)
        r_w = NODE_R + int(2 * beat)
        img, draw = _new_frame()
        _draw_header(draw, "initialise  re-entered (yellow)")
        _draw_spine(draw, 60, spine_bot)
        for i, (lbl, prm, ret, col) in enumerate(REFRACTED_NODES):
            c = NODE_PROC if i == 0 else col
            _draw_node(draw, SPINE_X, node_ys[i], c, lbl, prm, ret)
        _glow_circle(draw, SPINE_X, node_ys[0], r_w, NODE_PROC)
        frames.append(img.convert("P", palette=Image.ADAPTIVE))

    # Phase E: return pulse travels back down from f1 to f5
    for step in range(int(fps * 0.7)):
        t = step / int(fps * 0.7)
        img, draw = _base_frame("Mode: LIVE TRACE  ·  returned")
        spark_y = node_ys[0] + (node_ys[4] - node_ys[0]) * t
        draw.ellipse((SPINE_X-3, spark_y-3, SPINE_X+3, spark_y+3),
                     fill=PULSE_UP)
        frames.append(img.convert("P", palette=Image.ADAPTIVE))

    for _ in range(int(fps * 1.2)):
        img, draw = _base_frame("Mode: LIVE TRACE  ·  done")
        frames.append(img.convert("P", palette=Image.ADAPTIVE))

    frames[0].save(out_path, save_all=True, append_images=frames[1:],
                   duration=int(1000/fps), loop=0, disposal=2)
    print(f"  ✔  {out_path}  ({len(frames)} frames)")


# ---------------------------------------------------------------------------
# Demo 5 — crash blame chain
#
# A calls B calls C.  C raises an exception.  The HUD turns C red and emits
# the CRIT_FAIL label, then an upward blame pulse propagates to B, then A.
# Each node shows the error text on the returns line like:
#   RAISED: ValueError (from C)
#   called C which raised (from B)
#   cascade from B (from A)
# ---------------------------------------------------------------------------
BLAME_CHAIN = [
    ("pipeline_run",   "cfg: dict",          None,                          NODE_OK),
    ("transform_batch","batch: Tensor[64]",  None,                          NODE_OK),
    ("matrix_mul",     "w: Tensor[10,10]",   "RAISED: ShapeError(64,10)",   NODE_ERR),
]


def build_demo_crash_blame(out_path):
    fps = 18
    frames = []
    node_ys = [90 + i * NODE_GAP for i in range(len(BLAME_CHAIN))]
    spine_bot = node_ys[-1] + 30

    # Phase A: all nodes arrive as yellow (processing)
    proc_state = [NODE_PROC] * len(BLAME_CHAIN)

    for _ in range(int(fps * 0.5)):
        img, draw = _new_frame()
        _draw_header(draw, "Mode: LIVE TRACE  ·  running…")
        _draw_spine(draw, 60, spine_bot)
        for i, (lbl, prm, _, _col) in enumerate(BLAME_CHAIN):
            _draw_node(draw, SPINE_X, node_ys[i], proc_state[i], lbl, prm, None)
        frames.append(img.convert("P", palette=Image.ADAPTIVE))

    # Phase B: C (idx=2) crashes — flash red
    for step in range(int(fps * 0.5)):
        t = step / int(fps * 0.5)
        blended = _blend(NODE_PROC + (255,), NODE_ERR + (255,), t)
        col = blended[:3] + (255,)
        lbl, prm, ret, _ = BLAME_CHAIN[2]
        img, draw = _new_frame()
        _draw_header(draw, "CRITICAL: ShapeError in matrix_mul")
        _draw_spine(draw, 60, spine_bot)
        for i in range(len(BLAME_CHAIN)):
            c = col if i == 2 else proc_state[i]
            r = "RAISED: ShapeError(64,10)" if (i == 2 and t > 0.5) else None
            _draw_node(draw, SPINE_X, node_ys[i], c,
                       BLAME_CHAIN[i][0], BLAME_CHAIN[i][1], r,
                       err=(i == 2 and t > 0.5))
        frames.append(img.convert("P", palette=Image.ADAPTIVE))

    proc_state[2] = NODE_ERR
    blame_returns = [None, None, "RAISED: ShapeError(64,10)"]

    # Phase C–D: blame pulse travels upward node-by-node (C→B, B→A)
    for blame_idx in [2, 1]:
        target_idx = blame_idx - 1
        blame_msg  = (
            "called matrix_mul which raised" if blame_idx == 2
            else "cascade from transform_batch"
        )
        travel_steps = int(fps * 0.6)
        for step in range(travel_steps):
            t = step / travel_steps
            img, draw = _new_frame()
            _draw_header(draw, f"Blame propagating → {BLAME_CHAIN[target_idx][0]}")
            _draw_spine(draw, 60, spine_bot)
            for i in range(len(BLAME_CHAIN)):
                _draw_node(draw, SPINE_X, node_ys[i], proc_state[i],
                           BLAME_CHAIN[i][0], BLAME_CHAIN[i][1], blame_returns[i],
                           err=(proc_state[i] is NODE_ERR))
            spark_y = node_ys[blame_idx] + (node_ys[target_idx] - node_ys[blame_idx]) * t
            draw.ellipse((SPINE_X-4, spark_y-4, SPINE_X+4, spark_y+4),
                         fill=PULSE_REFRACTED)
            frames.append(img.convert("P", palette=Image.ADAPTIVE))

        # Flash the target node red
        for step in range(int(fps * 0.35)):
            t2 = step / int(fps * 0.35)
            blended = _blend(NODE_PROC + (255,), NODE_ERR + (255,), t2)
            col = blended[:3] + (255,)
            img, draw = _new_frame()
            _draw_header(draw, f"{BLAME_CHAIN[target_idx][0]}  blamed")
            _draw_spine(draw, 60, spine_bot)
            for i in range(len(BLAME_CHAIN)):
                c = col if i == target_idx else proc_state[i]
                _draw_node(draw, SPINE_X, node_ys[i], c,
                           BLAME_CHAIN[i][0], BLAME_CHAIN[i][1], blame_returns[i],
                           err=(proc_state[i] is NODE_ERR))
            frames.append(img.convert("P", palette=Image.ADAPTIVE))

        proc_state[target_idx] = NODE_ERR
        blame_returns[target_idx] = blame_msg

    # Phase E: final hold — all three nodes red with blame text
    for _ in range(int(fps * 1.8)):
        img, draw = _new_frame()
        _draw_header(draw, "CRASH — 3 frames blamed")
        _draw_spine(draw, 60, spine_bot)
        for i in range(len(BLAME_CHAIN)):
            _draw_node(draw, SPINE_X, node_ys[i], NODE_ERR,
                       BLAME_CHAIN[i][0], BLAME_CHAIN[i][1], blame_returns[i], err=True)
        frames.append(img.convert("P", palette=Image.ADAPTIVE))

    frames[0].save(out_path, save_all=True, append_images=frames[1:],
                   duration=int(1000/fps), loop=0, disposal=2)
    print(f"  ✔  {out_path}  ({len(frames)} frames)")


# ---------------------------------------------------------------------------
# Demo 6 — streaming pulses (FLOW_REAL_TIME=True, data flowing fast)
#
# Three nodes exist.  A burst of 8 rapid pulses travels down the spine
# simultaneously (offset in time), mimicking a data stream.  Then an equal
# burst of return pulses travels back up.
# ---------------------------------------------------------------------------
def build_demo_streaming(out_path):
    fps = 24
    frames = []

    stream_nodes = [
        ("ingest",    "url: str",         "Chunk[256]", NODE_OK),
        ("tokenise",  "chunk: Chunk[256]","Token[128]", NODE_OK),
        ("embed",     "tokens: Token[]",  "Vec[768]",   NODE_OK),
    ]
    node_ys   = [90 + i * NODE_GAP for i in range(len(stream_nodes))]
    spine_bot = node_ys[-1] + 30

    STREAM_COUNT = 10   # pulses per burst
    PULSE_TRAVEL = fps  # frames to cross the full spine height

    def _draw_scene(draw, header, pulses_down, pulses_up, phase):
        """pulses_down / pulses_up: list of float progress values 0→1."""
        _draw_header(draw, header)
        _draw_spine(draw, 60, spine_bot)
        for i, (lbl, prm, ret, col) in enumerate(stream_nodes):
            # Heartbeat on node
            beat = 1.0 + 0.1 * math.sin(phase * 0.3 + i)
            _glow_circle(draw, SPINE_X, node_ys[i], int(NODE_R * beat), col)
            draw.text((SPINE_X + 22, node_ys[i] - 8), lbl, fill=TEXT_LABEL, font=FONT_LABEL)
            draw.text((SPINE_X + 22, node_ys[i] - 28), prm, fill=TEXT_PARAM, font=FONT_PARAM)
            draw.text((SPINE_X + 22, node_ys[i] + 14), ret, fill=TEXT_PARAM, font=FONT_PARAM)

        top_y = node_ys[0]
        bot_y = node_ys[-1]
        span  = bot_y - top_y

        for t in pulses_down:
            y = top_y + span * t
            alpha = int(230 * (1 - abs(t - 0.5)))
            draw.ellipse((SPINE_X-3, y-3, SPINE_X+3, y+3),
                         fill=(*PULSE_DOWN[:3], max(80, alpha)))

        for t in pulses_up:
            y = bot_y - span * t
            alpha = int(230 * (1 - abs(t - 0.5)))
            draw.ellipse((SPINE_X+1, y-3, SPINE_X+7, y+3),
                         fill=(*PULSE_UP[:3], max(80, alpha)))

    total_frames = fps * 3
    phase = 0

    # Stagger offsets so pulses are evenly spread over one travel duration
    stagger = PULSE_TRAVEL / STREAM_COUNT

    for f in range(total_frames):
        img, draw = _new_frame()
        # Each pulse starts STREAM_COUNT frames apart
        pulses_down = []
        pulses_up   = []
        for k in range(STREAM_COUNT):
            age_d = f - k * stagger
            if 0 <= age_d <= PULSE_TRAVEL:
                pulses_down.append(age_d / PULSE_TRAVEL)
            # Return burst starts after down burst clears
            age_u = f - PULSE_TRAVEL - k * stagger
            if 0 <= age_u <= PULSE_TRAVEL:
                pulses_up.append(age_u / PULSE_TRAVEL)

        half = total_frames // 2
        header = (
            f"Stream burst ↓  ({len(pulses_down)} packets)"
            if f < half else
            f"Return burst ↑  ({len(pulses_up)} packets)"
        )
        _draw_scene(draw, header, pulses_down, pulses_up, phase)
        frames.append(img.convert("P", palette=Image.ADAPTIVE))
        phase += 1

    frames[0].save(out_path, save_all=True, append_images=frames[1:],
                   duration=int(1000/fps), loop=0, disposal=2)
    print(f"  ✔  {out_path}  ({len(frames)} frames)")


# ---------------------------------------------------------------------------
# Demo 7 — all node states
# ---------------------------------------------------------------------------
def build_demo_all_states(out_path):
    fps = 18
    frames = []

    states = [
        ("ghost_node",   NODE_GHOST, "processing…", None,         "ghost (idle)"),
        ("active_func",  NODE_PROC,  "x: int=42",   None,         "processing"),
        ("done_func",    NODE_OK,    "x: int=42",   "int=84",     "success"),
        ("bad_func",     NODE_ERR,   "CRIT_FAIL [TYPE]: expected int, got str",
                                     "RAISED: TypeError", "nuke"),
    ]

    node_ys   = [70 + i * 120 for i in range(len(states))]
    spine_bot = node_ys[-1] + 40

    for frame_n in range(fps * 4):
        t = frame_n / fps
        img, draw = _new_frame(h=580)
        _draw_header(draw, "All node states")
        draw.line((SPINE_X, 50, SPINE_X, spine_bot), fill=SPINE, width=2)

        for i, (lbl, col, params, returns, caption) in enumerate(states):
            cy = node_ys[i]
            # Heartbeat for processing node
            r = NODE_R
            if col is NODE_PROC:
                r = NODE_R + int(2 * math.sin(t * 5))
            _glow_circle(draw, SPINE_X, cy, r, col)
            draw.text((SPINE_X + 22, cy - 8),  lbl,    fill=TEXT_LABEL, font=FONT_LABEL)
            draw.text((SPINE_X + 22, cy - 28), params, fill=(TEXT_ERR if col is NODE_ERR else TEXT_PARAM), font=FONT_PARAM)
            if returns:
                draw.text((SPINE_X + 22, cy + 14), returns,
                          fill=(TEXT_ERR if col is NODE_ERR else TEXT_PARAM), font=FONT_PARAM)
            draw.text((SPINE_X - 165, cy - 4), f"[{caption}]", fill=TEXT_DIM, font=FONT_HOVER)

        draw.point((0, 0), fill=(frame_n % 256, 0, 0, 255))
        frames.append(img.convert("P", palette=Image.ADAPTIVE))

    frames[0].save(out_path, save_all=True, append_images=frames[1:],
                   duration=int(1000/fps), loop=0, disposal=2)
    print(f"  ✔  {out_path}  ({len(frames)} frames)")


# ---------------------------------------------------------------------------
def main():
    out_dir = Path(__file__).resolve().parent.parent / "demo_gifs"
    out_dir.mkdir(exist_ok=True)
    print(f"Building demo GIFs into {out_dir}")

    build_demo_pulse(       out_dir / "demo_pulse.gif")
    build_demo_returns(     out_dir / "demo_returns.gif")
    build_demo_hover(       out_dir / "demo_hover.gif")
    build_demo_refracted(   out_dir / "demo_refracted.gif")
    build_demo_crash_blame( out_dir / "demo_crash_blame.gif")
    build_demo_streaming(   out_dir / "demo_streaming.gif")
    build_demo_all_states(  out_dir / "demo_all_states.gif")

    print(f"\nDone — {len(list(out_dir.glob('*.gif')))} GIFs in {out_dir}")


if __name__ == "__main__":
    main()
