"""The orb: a living ball that shows what the assistant is doing.

    idle        slow breathing, a soft glow
    listening   rings spread out of the ball
    processing  a bright arc runs around it
    speaking    the ball pulses, rings follow the voice
    paused      gray and still: the microphone is off

The pictures are made with numpy (a plasma of three colors inside a glossy sphere, soft edges, no jagged lines) and
played as a short loop. render_frame() has no Tk in it and can be tested without a screen; OrbPlayer shows the loop
on a canvas (so the ball can lie over a glow or a wallpaper of the same canvas), OrbView is a canvas of its own.
"""
import math
import queue
import threading
from collections import OrderedDict
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageTk

import tkinter as tk

from src.interface import palette
from src.interface import theme as t

# The radius of the ball as a share of the size of the picture: the rest is for the glow and the rings
R_FRACTION = 0.27


@dataclass(frozen=True)
class StyleOfState:
    frames: int          # frames in the loop
    fps: int             # how fast the loop is played
    rings: int           # rings that spread from the ball
    swirl: int           # how many times the colors turn inside the ball during the loop
    pulse: float         # how much the ball swells and shrinks
    glow: float          # strength of the glow around it
    arc: bool = False    # a bright arc runs around the ball
    gray: float = 0.0    # 1 means the ball has no color at all


STATE_STYLES = {
    "idle": StyleOfState(frames=40, fps=10, rings=0, swirl=1, pulse=0.018, glow=0.34),
    "listening": StyleOfState(frames=32, fps=24, rings=2, swirl=2, pulse=0.022, glow=0.42),
    "processing": StyleOfState(frames=32, fps=24, rings=0, swirl=3, pulse=0.010, glow=0.36, arc=True),
    "speaking": StyleOfState(frames=32, fps=24, rings=3, swirl=2, pulse=0.055, glow=0.50),
    "paused": StyleOfState(frames=1, fps=1, rings=0, swirl=0, pulse=0.0, glow=0.0, gray=0.88),
}


@dataclass(frozen=True)
class OrbColors:
    """Colors of the ball in #RRGGBB; ring is the color of the state (the rings and the arc)."""
    first: str
    second: str
    third: str
    ring: str


def colors_for(state, dark):
    """The colors of the ball for the current palette and the given state."""
    vivid = t.pick(t.ACCENT_VIVID, dark)
    lightness, chroma, hue = palette.hex_to_oklch(vivid)
    ring = {
        "listening": t.OK, "processing": t.WARN, "speaking": t.VIOLET, "idle": t.ACCENT_VIVID, "paused": t.OFF,
    }.get(state, t.ACCENT_VIVID)
    return OrbColors(
        first=vivid,
        second=palette.oklch_to_hex(lightness + 0.07, chroma * 0.95, hue + 38),
        third=palette.oklch_to_hex(lightness - 0.08, chroma, hue - 34),
        ring=t.pick(ring, dark),
    )


def _rgb(color):
    return np.array(palette.hex_to_rgb(color), dtype=np.float32) / 255.0


_GRID_CACHE = {}


def _grid(size):
    if size not in _GRID_CACHE:
        axis = (np.arange(size, dtype=np.float32) + 0.5) - size / 2
        x, y = np.meshgrid(axis, axis)
        _GRID_CACHE[size] = (x, y, np.hypot(x, y), np.arctan2(y, x))
        if len(_GRID_CACHE) > 8:
            _GRID_CACHE.pop(next(iter(_GRID_CACHE)))
    return _GRID_CACHE[size]


def render_frame(size, phase, state, colors, dark):
    """One picture of the loop: phase is 0..1 (0 and 1 are the same picture), the result is RGBA, size x size."""
    style = STATE_STYLES.get(state, STATE_STYLES["idle"])
    x, y, r, theta = _grid(size)
    turn = 2 * math.pi * phase
    radius = size * R_FRACTION
    # The ball swells and shrinks; the sum of two waves feels less mechanical than one
    slow = state in ("idle", "paused")
    swell = style.pulse * (math.sin(turn * (1 if slow else 3)) + 0.5 * math.sin(turn * (2 if slow else 5) + 1.3))
    radius_now = radius * (1.0 + swell)
    edge = max(1.0, size / 180.0)

    # The layers are put one over another; rgb is premultiplied by alpha, the picture is "un-multiplied" at the end
    rgb = np.zeros((size, size, 3), dtype=np.float32)
    alpha = np.zeros((size, size), dtype=np.float32)

    def over(color, layer_alpha):
        nonlocal rgb, alpha
        layer_alpha = np.clip(layer_alpha, 0.0, 1.0)
        cover = layer_alpha[..., None]
        rgb = np.asarray(color, dtype=np.float32) * cover + rgb * (1.0 - cover)
        alpha = layer_alpha + alpha * (1.0 - layer_alpha)

    # --- glow (it dies out before the edge of the picture, so no square is seen on the background) ---
    if style.glow > 0:
        strength = style.glow * (0.85 if dark else 0.62) * (1.0 + 0.16 * math.sin(turn) + 1.5 * swell)
        falloff = np.exp(-(np.maximum(r - radius_now * 0.75, 0.0) / (radius_now * 0.78)) ** 2)
        window = np.clip((size / 2 - r) / (size * 0.17), 0.0, 1.0)
        falloff = falloff * window * window * (3.0 - 2.0 * window)
        over(_rgb(colors.first), strength * falloff)

    # --- rings that spread out of the ball ---
    thickness = max(1.4, size * 0.0075)
    for k in range(style.rings):
        progress = (phase + k / style.rings) % 1.0
        ring_radius = radius_now * (1.05 + 0.78 * progress)
        strength = 0.62 * (1.0 - progress) ** 1.7
        over(_rgb(colors.ring), strength * np.clip(1.0 - np.abs(r - ring_radius) / thickness, 0.0, 1.0))

    # --- the arc that runs around the ball ---
    if style.arc:
        behind = ((theta - turn) % (2 * math.pi)) / (2 * math.pi)       # 0 at the head, 1 at the end of the tail
        tail = np.clip(1.0 - behind / 0.72, 0.0, 1.0) ** 2
        arc_radius = radius_now * 1.30
        over(_rgb(colors.ring), 0.95 * tail * np.clip(1.0 - np.abs(r - arc_radius) / (thickness * 1.25), 0.0, 1.0))

    # --- the ball: a plasma of three colors under a glossy shell ---
    nx, ny = x / radius_now, y / radius_now
    inside = np.clip(1.0 - (nx * nx + ny * ny), 0.0, 1.0)
    nz = np.sqrt(inside)
    swirl = max(style.swirl, 0)
    # The colors flow a little: the coordinates are bent by slow waves (whole numbers of turns keep the loop closed)
    bend = 0.10 if swirl else 0.0
    wx = nx + bend * np.sin(3.1 * ny + turn * swirl)
    wy = ny + bend * np.cos(2.7 * nx - turn * swirl)
    weights, plasma = [], []
    for index, color in enumerate((colors.first, colors.second, colors.third)):
        angle = turn * swirl * (1 if index != 1 else -1) + index * 2.1
        cx, cy = 0.5 * math.cos(angle), 0.5 * math.sin(angle)
        w = np.exp(-(((wx - cx) ** 2 + (wy - cy) ** 2) / (2 * 0.55 ** 2)))
        weights.append(w)
        plasma.append(w[..., None] * _rgb(color))
    base = 0.28
    color = (sum(plasma) + base * _rgb(colors.first)) / (sum(weights) + base)[..., None]
    # light comes from above on the left: the ball gets round
    light = np.clip(nx * -0.42 + ny * -0.52 + nz * 0.74, 0.0, 1.0)
    color = color * ((0.55 if dark else 0.62) + 0.62 * light)[..., None]
    highlight = np.clip(nx * -0.30 + ny * -0.42 + nz * 0.85, 0.0, 1.0) ** 38
    rim = (1.0 - nz) ** 3
    color = color + 0.62 * highlight[..., None] + 0.20 * rim[..., None] * _rgb(colors.second)
    if style.gray:
        luma = (color * np.array([0.30, 0.59, 0.11], dtype=np.float32)).sum(axis=-1, keepdims=True)
        color = color * (1 - style.gray) + luma * style.gray * 0.82
    shell = np.clip((radius_now - r) / edge + 0.5, 0.0, 1.0)
    if style.gray:
        shell = shell * 0.78
    over(np.clip(color, 0.0, 1.0), shell)

    straight = np.where(alpha[..., None] > 1e-5, rgb / np.maximum(alpha[..., None], 1e-5), 0.0)
    out = np.dstack([np.clip(straight, 0.0, 1.0) * 255.0, np.clip(alpha, 0.0, 1.0)[..., None] * 255.0])
    return Image.fromarray(out.astype(np.uint8), "RGBA")


def render_loop(size, state, colors, dark):
    style = STATE_STYLES.get(state, STATE_STYLES["idle"])
    return [render_frame(size, index / style.frames, state, colors, dark) for index in range(style.frames)]


# Loops that are already made: switching between the states of the assistant does not render them again.
# They are kept within a budget of memory (an RGBA picture takes 4 bytes per pixel)
_LOOPS = OrderedDict()
_LOOPS_LOCK = threading.Lock()
_LOOPS_BUDGET = 72 * 1024 * 1024


def loop_key(size, state, colors, dark):
    return (size, state, colors, dark)


def loop_bytes(frames):
    return sum(frame.width * frame.height * 4 for frame in frames)


def cached_loop(key):
    with _LOOPS_LOCK:
        frames = _LOOPS.get(key)
        if frames is not None:
            _LOOPS.move_to_end(key)
        return frames


def remember_loop(key, frames):
    with _LOOPS_LOCK:
        _LOOPS[key] = frames
        _LOOPS.move_to_end(key)
        total = sum(loop_bytes(item) for item in _LOOPS.values())
        while total > _LOOPS_BUDGET and len(_LOOPS) > 1:
            _, dropped = _LOOPS.popitem(last=False)
            total -= loop_bytes(dropped)


def clear_loops():
    with _LOOPS_LOCK:
        _LOOPS.clear()


class OrbPlayer:
    """Shows the ball on a canvas as an image item at (x, y). The loop is made in a thread of its own and starts
    when it is ready; until then the first picture is shown, so the ball is never empty."""

    def __init__(self, canvas, x, y, size, state="idle", dark=True, animated=True):
        self.canvas = canvas
        self.size = size
        self.state = state
        self.dark = dark
        self.animated = animated
        self.item = canvas.create_image(x, y)
        self._frames = []        # PIL images of the loop that is shown
        self._photos = {}        # PhotoImage by the number of the frame: made when the frame is shown first
        self._index = 0
        self._job = None
        self._poll_job = None
        self._generation = 0     # grows with every request: an outdated loop is thrown away
        self._ready = queue.Queue()
        self._waiting = None     # the number of the request whose loop is being made
        self._closed = False
        self.visible = True      # False while the page of the ball is covered by another one: it waits then
        self.request()

    # ---------- What is shown ----------

    def configure(self, state=None, dark=None, animated=None):
        changed = False
        if state is not None and state != self.state:
            self.state, changed = state, True
        if dark is not None and dark != self.dark:
            self.dark, changed = dark, True
        if animated is not None and animated != self.animated:
            self.animated, changed = animated, True
        if changed:
            self.request()

    def move(self, x, y):
        self.canvas.coords(self.item, x, y)

    def set_visible(self, visible):
        self.visible = bool(visible)

    def resize(self, size):
        if size != self.size:
            self.size = size
            self._frames = []
            self.request()

    def refresh_palette(self):
        """The accent has changed: the loop is made again."""
        self._frames = []
        self.request()

    def request(self):
        if self._closed:
            return
        self._generation += 1
        generation = self._generation
        self._waiting = None
        colors = colors_for(self.state, self.dark)
        key = loop_key(self.size, self.state, colors, self.dark)
        frames = cached_loop(key)
        if frames is not None:
            self._install(frames if self.animated else frames[:1])
            return
        # Until the loop is ready (or when nothing moves) a still picture of the state is shown
        self._install([render_frame(self.size, 0.0, self.state, colors, self.dark)])
        if not self.animated or STATE_STYLES.get(self.state, STATE_STYLES["idle"]).frames <= 1:
            return
        size, state, dark = self.size, self.state, self.dark

        def work():
            self._ready.put((generation, key, render_loop(size, state, colors, dark)))

        self._waiting = generation
        threading.Thread(target=work, daemon=True, name="orb-frames").start()
        self._schedule_poll()

    def _schedule_poll(self):
        if self._poll_job is None and not self._closed:
            self._poll_job = self.canvas.after(30, self._poll)

    def _poll(self):
        self._poll_job = None
        if self._closed:
            return
        try:
            while True:
                generation, key, frames = self._ready.get_nowait()
                remember_loop(key, frames)
                if generation == self._generation:
                    self._waiting = None
                    self._install(frames)
        except queue.Empty:
            pass
        if self._waiting is not None:
            self._schedule_poll()

    def _install(self, frames):
        self._frames = frames
        self._photos = {}
        self._index = 0
        self._show(0)
        self._restart_timer()

    def _show(self, index):
        photo = self._photos.get(index)
        if photo is None:
            photo = self._photos[index] = ImageTk.PhotoImage(self._frames[index])
        self.canvas.itemconfigure(self.item, image=photo)

    # ---------- Playing ----------

    def _restart_timer(self):
        if self._job is not None:
            self.canvas.after_cancel(self._job)
            self._job = None
        if self.animated and len(self._frames) > 1 and not self._closed:
            self._job = self.canvas.after(self._delay(), self._tick)

    def _delay(self):
        return max(15, int(1000 / STATE_STYLES.get(self.state, STATE_STYLES["idle"]).fps))

    def _tick(self):
        self._job = None
        if self._closed:
            return
        # Nobody sees the ball on a page that is not open: it waits without drawing
        if self.visible and self.canvas.winfo_ismapped() and self.canvas.winfo_viewable():
            self._index = (self._index + 1) % len(self._frames)
            self._show(self._index)
            delay = self._delay()
        else:
            delay = 400
        self._job = self.canvas.after(delay, self._tick)

    def close(self):
        """Stops the playing; the picture on the canvas is left as it is."""
        self._closed = True
        self._generation += 1
        for job in (self._job, self._poll_job):
            if job is not None:
                try:
                    self.canvas.after_cancel(job)
                except Exception:
                    pass
        self._job = self._poll_job = None


class OrbView(tk.Canvas):
    """The ball on a canvas of its own."""

    def __init__(self, master, size, background, state="idle", dark=True, animated=True):
        super().__init__(master, width=size, height=size, bg=background, highlightthickness=0, bd=0)
        self.size = size
        self.player = OrbPlayer(self, size // 2, size // 2, size, state=state, dark=dark, animated=animated)

    def configure_orb(self, state=None, dark=None, animated=None, background=None):
        if background is not None:
            self.configure(bg=background)
        self.player.configure(state=state, dark=dark, animated=animated)

    def refresh_palette(self):
        self.player.refresh_palette()

    def set_visible(self, visible):
        self.player.set_visible(visible)

    def destroy(self):
        self.player.close()
        super().destroy()
