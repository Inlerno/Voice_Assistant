"""Drawn graphics: icons and the soft background glow of the home page (Pillow, no image files)."""
from functools import lru_cache

import customtkinter as ctk
import numpy as np
from PIL import Image, ImageDraw

from src.interface import theme

# Icons are drawn on a 24x24 grid with this supersampling and scaled down, so the lines are smooth
SUPERSAMPLE = 6


def _hex_to_rgb(color):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


class _Pen:
    """Draws lines with round ends on a supersampled canvas using the 24x24 icon grid."""

    def __init__(self, size, color, stroke):
        self.k = size * SUPERSAMPLE / 24.0
        self.image = Image.new("RGBA", (size * SUPERSAMPLE, size * SUPERSAMPLE), (0, 0, 0, 0))
        self.draw = ImageDraw.Draw(self.image)
        self.color = _hex_to_rgb(color) + (255,)
        self.width = max(2, round(stroke * self.k))

    def pts(self, points):
        return [(x * self.k, y * self.k) for x, y in points]

    def line(self, points):
        points = self.pts(points)
        self.draw.line(points, fill=self.color, width=self.width, joint="curve")
        radius = self.width / 2
        for x, y in (points[0], points[-1]):
            self.draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=self.color)

    def polygon(self, points, close=True):
        points = list(points) + ([points[0]] if close else [])
        self.line(points)

    def rounded_rect(self, box, radius):
        self.draw.rounded_rectangle([v * self.k for v in box], radius=radius * self.k,
                                    outline=self.color, width=self.width)

    def arc(self, box, start, end):
        self.draw.arc([v * self.k for v in box], start, end, fill=self.color, width=self.width)

    def dot(self, center, radius):
        cx, cy = center[0] * self.k, center[1] * self.k
        r = radius * self.k
        self.draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=self.color)

    def fill_polygon(self, points):
        self.draw.polygon(self.pts(points), fill=self.color)

    def result(self, size):
        return self.image.resize((size, size), Image.LANCZOS)


def _draw_icon(name, size, color, stroke=1.9):
    pen = _Pen(size, color, stroke)
    if name == "home":
        pen.line([(3, 11), (12, 3.5), (21, 11)])
        pen.line([(5.5, 9.5), (5.5, 20.5), (18.5, 20.5), (18.5, 9.5)])
        pen.line([(10, 20.5), (10, 14.5), (14, 14.5), (14, 20.5)])
    elif name == "terminal":
        pen.line([(5, 7), (11, 12), (5, 17)])
        pen.line([(13, 17.5), (19, 17.5)])
    elif name == "cube":
        pen.polygon([(12, 3), (20, 7.5), (20, 16.5), (12, 21), (4, 16.5), (4, 7.5)])
        pen.line([(4, 7.5), (12, 12), (20, 7.5)])
        pen.line([(12, 12), (12, 21)])
    elif name in ("mic", "mic_off"):
        pen.rounded_rect((9, 3, 15, 14.5), 3)
        pen.arc((5.5, 7.5, 18.5, 19.5), 0, 180)
        pen.line([(12, 19.5), (12, 22)])
        pen.line([(9, 22), (15, 22)])
        if name == "mic_off":
            pen.line([(4, 4), (20, 20)])
    elif name == "document":
        pen.rounded_rect((5, 3, 19, 21), 2.5)
        pen.line([(8.5, 9), (15.5, 9)])
        pen.line([(8.5, 13), (15.5, 13)])
        pen.line([(8.5, 17), (12.5, 17)])
    elif name == "sliders":
        for y, x in ((7, 9), (12, 15), (17, 8)):
            pen.line([(4, y), (20, y)])
            pen.dot((x, y), 2.3)
    elif name == "send":
        pen.line([(12, 19), (12, 5.5)])
        pen.line([(6.5, 11), (12, 5.5), (17.5, 11)])
    elif name == "plus":
        pen.line([(12, 5), (12, 19)])
        pen.line([(5, 12), (19, 12)])
    elif name == "chevron":
        pen.line([(6, 9), (12, 15), (18, 9)])
    elif name == "play":
        pen.polygon([(8, 5), (19, 12), (8, 19)])
    elif name == "clock":
        pen.arc((3.5, 3.5, 20.5, 20.5), 0, 360)
        pen.line([(12, 7.5), (12, 12), (15.5, 14)])
    elif name == "check":
        pen.line([(5, 12.5), (10, 17.5), (19, 7)])
    elif name == "close":
        pen.line([(6.5, 6.5), (17.5, 17.5)])
        pen.line([(17.5, 6.5), (6.5, 17.5)])
    elif name == "grid":
        for x, y in ((4, 4), (13.5, 4), (4, 13.5), (13.5, 13.5)):
            pen.rounded_rect((x, y, x + 6.5, y + 6.5), 1.6)
    elif name == "speaker":
        pen.polygon([(4, 9.5), (8, 9.5), (13, 5.5), (13, 18.5), (8, 14.5), (4, 14.5)])
        pen.arc((12, 8.5, 18, 15.5), -60, 60)
        pen.arc((12, 5, 21, 19), -60, 60)
    elif name == "bell":
        pen.line([(5.5, 17.5), (18.5, 17.5)])
        pen.line([(7, 17.5), (7, 11), (8.5, 7.5), (12, 6), (15.5, 7.5), (17, 11), (17, 17.5)])
        pen.arc((10, 17.5, 14, 21.5), 0, 180)
        pen.line([(12, 3.5), (12, 6)])
    elif name == "hourglass":
        pen.line([(6.5, 3.5), (17.5, 3.5)])
        pen.line([(6.5, 20.5), (17.5, 20.5)])
        pen.line([(8, 3.5), (8, 8), (12, 12), (16, 8), (16, 3.5)])
        pen.line([(8, 20.5), (8, 16), (12, 12), (16, 16), (16, 20.5)])
    elif name == "sparkle":
        pen.fill_polygon(_sparkle_points())
    else:
        raise ValueError(f"Unknown icon: {name}")
    return pen.result(size)


def _sparkle_points():
    """A four-pointed star: the sparkle of the Astra logo."""
    return [(12, 1.5), (14.1, 9.9), (22.5, 12), (14.1, 14.1), (12, 22.5), (9.9, 14.1), (1.5, 12), (9.9, 9.9)]


@lru_cache(maxsize=None)
def icon(name, color, size=20):
    """An icon as a CTkImage; color is a (light, dark) pair of hex colors."""
    light = _draw_icon(name, size, color[0])
    dark = _draw_icon(name, size, color[1])
    return ctk.CTkImage(light_image=light, dark_image=dark, size=(size, size))


def sparkle_image(size, color):
    """The sparkle as a plain Pillow image (for the canvas of the home page)."""
    return _draw_icon("sparkle", size, color)


# ---------- Background glow of the home page ----------

# Soft blurred blobs: center x, center y, radius x, radius y (parts of the image size), color, strength
_GLOW = {
    False: ((0.50, 0.17, 0.34, 0.17, "#F0C9BB", 0.95),
            (0.14, 0.28, 0.26, 0.20, "#EBBDB1", 0.80),
            (0.88, 0.22, 0.28, 0.20, "#F3D6C2", 0.85)),
    True: ((0.50, 0.17, 0.34, 0.17, "#7A4338", 0.95),
           (0.14, 0.28, 0.26, 0.20, "#5C2F33", 0.85),
           (0.88, 0.22, 0.28, 0.20, "#6B3A2E", 0.85)),
}
# The glow lives in the upper part of the page and fades out completely at this share of the height,
# so the input field below it sits on a flat background
GLOW_FULL_UNTIL = 0.14
GLOW_GONE_AT = 0.46


def _smoothstep(edge0, edge1, x):
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


@lru_cache(maxsize=4)
def glow_image(dark, width=480, height=300):
    """The background of the home page: a few soft warm blobs on the flat background color."""
    base = _hex_to_rgb(theme.pick(theme.BG, dark))
    ys, xs = np.mgrid[0:height, 0:width]
    x, y = xs / (width - 1), ys / (height - 1)
    mask = 1.0 - _smoothstep(GLOW_FULL_UNTIL, GLOW_GONE_AT, y)
    out = np.empty((height, width, 3), dtype=np.float64)
    out[:] = base
    for cx, cy, rx, ry, color, strength in _GLOW[dark]:
        alpha = np.clip(strength * np.exp(-(((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2) / 2) * mask, 0, 1)
        out = out * (1 - alpha[..., None]) + np.array(_hex_to_rgb(color), dtype=np.float64) * alpha[..., None]
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")
