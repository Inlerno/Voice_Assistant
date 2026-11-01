"""Color schemes: from one accent color to the whole palette of the interface, for the light and the dark theme.

The colors are made in OKLCH (lightness, chroma, hue). Unlike HSL, the lightness there is what the eye sees, so every
hue gets the same contrast with the text, and a yellow accent is as readable as a blue one.

    scheme = build_scheme(accent_family("#3E6DD0"), tint=0.5, depth=0.0)
    scheme["BG"]  ->  ("#F5F6F9", "#14161C")      # (light theme, dark theme)

There is no Tk here: the module is plain Python and can be tested without a screen.
"""
import math
from dataclasses import dataclass

# ---------- Conversions ----------


def hex_to_rgb(color):
    color = color.lstrip("#")
    if len(color) == 3:
        color = "".join(ch * 2 for ch in color)
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb):
    return "#{:02X}{:02X}{:02X}".format(*(max(0, min(255, round(v))) for v in rgb))


def is_hex_color(value):
    if not isinstance(value, str):
        return False
    value = value.strip().lstrip("#")
    return len(value) in (3, 6) and all(ch in "0123456789abcdefABCDEF" for ch in value)


def normalize_hex(value, default="#7D3B35"):
    """'#abc' / 'ABCDEF' -> '#AABBCC'; anything that is not a color gives the default."""
    if not is_hex_color(value):
        return default
    return rgb_to_hex(hex_to_rgb(value.strip()))


def _to_linear(channel):
    channel /= 255.0
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def _from_linear(value):
    value = max(0.0, min(1.0, value))
    return 255.0 * (12.92 * value if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055)


def _linear_rgb_to_oklab(r, g, b):
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l, m, s = (math.copysign(abs(v) ** (1 / 3), v) for v in (l, m, s))
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def _oklab_to_linear_rgb(lightness, a, b):
    l = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)


def hex_to_oklch(color):
    """(lightness 0..1, chroma 0..~0.37, hue 0..360)."""
    lightness, a, b = _linear_rgb_to_oklab(*(_to_linear(v) for v in hex_to_rgb(color)))
    return lightness, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360


def _in_gamut(rgb, slack=1e-4):
    return all(-slack <= v <= 1 + slack for v in rgb)


def oklch_to_hex(lightness, chroma, hue):
    """The color as #RRGGBB. A color the screen cannot show loses chroma until it fits."""
    lightness = max(0.0, min(1.0, lightness))
    chroma = max(0.0, chroma)
    radians = math.radians(hue)

    def rgb_at(c):
        return _oklab_to_linear_rgb(lightness, c * math.cos(radians), c * math.sin(radians))

    if not _in_gamut(rgb_at(chroma)):
        low, high = 0.0, chroma
        for _ in range(24):
            middle = (low + high) / 2
            if _in_gamut(rgb_at(middle)):
                low = middle
            else:
                high = middle
        chroma = low
    return rgb_to_hex(tuple(_from_linear(v) for v in rgb_at(chroma)))


def luminance(color):
    r, g, b = (_to_linear(v) for v in hex_to_rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(first, second):
    """WCAG contrast: 1 (the same color) .. 21 (black on white). 4.5 is the norm for the text."""
    a, b = luminance(first), luminance(second)
    if a < b:
        a, b = b, a
    return (a + 0.05) / (b + 0.05)


def mix(first, second, amount):
    """The color between two colors (0 is the first, 1 is the second), mixed the way the eye does it."""
    l1, a1, b1 = _linear_rgb_to_oklab(*(_to_linear(v) for v in hex_to_rgb(first)))
    l2, a2, b2 = _linear_rgb_to_oklab(*(_to_linear(v) for v in hex_to_rgb(second)))
    rgb = _oklab_to_linear_rgb(l1 + (l2 - l1) * amount, a1 + (a2 - a1) * amount, b1 + (b2 - b1) * amount)
    return rgb_to_hex(tuple(_from_linear(v) for v in rgb))


def shift_hue(color, degrees, lightness=None, chroma_scale=1.0):
    """The same color turned around the color wheel (used for the second and third color of the orb)."""
    light, chroma, hue = hex_to_oklch(color)
    return oklch_to_hex(light if lightness is None else lightness, chroma * chroma_scale, hue + degrees)


# ---------- Accent colors ----------

@dataclass(frozen=True)
class AccentFamily:
    """What is taken from the accent color: its hue and how colorful it is."""
    hue: float
    chroma: float

    @property
    def neutral(self):
        return self.chroma < 0.02


# Ready accents: key, title, hue, chroma. The first one is the original look of Astra.
PRESETS = (
    ("bordo", "Бордо", 26.9, 0.093),
    ("coral", "Коралл", 40.0, 0.130),
    ("amber", "Янтарь", 72.0, 0.120),
    ("moss", "Мох", 130.0, 0.105),
    ("emerald", "Изумруд", 160.0, 0.105),
    ("ocean", "Океан", 225.0, 0.115),
    ("blue", "Синий", 258.0, 0.150),
    ("violet", "Фиалка", 300.0, 0.150),
    ("rose", "Роза", 350.0, 0.140),
    ("graphite", "Графит", 260.0, 0.0),
)
DEFAULT_PRESET = "bordo"
PRESET_FAMILIES = {key: AccentFamily(hue, chroma) for key, _, hue, chroma in PRESETS}
PRESET_TITLES = {key: title for key, title, _, _ in PRESETS}

MAX_CHROMA = 0.19


def accent_family(value):
    """A family from a preset key ('ocean') or from any #RRGGBB color chosen by the user."""
    if value in PRESET_FAMILIES:
        return PRESET_FAMILIES[value]
    if not is_hex_color(value):
        return PRESET_FAMILIES[DEFAULT_PRESET]
    _, chroma, hue = hex_to_oklch(normalize_hex(value))
    return AccentFamily(hue, min(chroma, MAX_CHROMA))


def accent_swatch(value):
    """The color to show on the button of an accent in the settings: how the accent looks on a light page."""
    return build_scheme(accent_family(value))["ACCENT"][0]


# ---------- The scheme ----------

# How much the neutral colors are tinted by the accent hue (0 is plain gray) and how deep the dark theme is
TINT_RANGE = (0.0, 1.0)
DEFAULT_TINT = 0.5
DEPTH_RANGE = (-1.0, 1.0)
DEPTH_STEP = 0.05          # the lightness the dark backgrounds move by when depth is 1


def _clamp(value, bounds):
    return max(bounds[0], min(bounds[1], value))


def _make(light, chroma, hue):
    return oklch_to_hex(light, chroma, hue)


def _readable(fill, text, minimum):
    """Moves the fill away from the text color until the text on it reads (contrast of at least minimum)."""
    light, chroma, hue = hex_to_oklch(fill)
    toward_dark = luminance(text) > luminance(fill)
    for _ in range(40):
        if contrast_ratio(fill, text) >= minimum:
            break
        light += -0.01 if toward_dark else 0.01
        fill = oklch_to_hex(light, chroma, hue)
    return fill


def build_scheme(family, tint=DEFAULT_TINT, depth=0.0):
    """Every color of the interface as (light theme, dark theme) pairs of #RRGGBB.

    tint  -- 0..1: how much the backgrounds take the hue of the accent (0.5 is the original look)
    depth -- -1..1: the dark theme made deeper (towards black) or lighter
    """
    tint = _clamp(tint, TINT_RANGE)
    depth = _clamp(depth, DEPTH_RANGE)
    hue, chroma = family.hue, family.chroma
    neutral = family.neutral
    if neutral:
        # A gray accent has no hue to tint the backgrounds with: they stay almost plain gray
        tint *= 0.3
    s = depth * DEPTH_STEP
    # chroma of the backgrounds: 0.017 in the dark theme and 0.008 in the light one at the original tint
    cd = 0.034 * tint
    cl = 0.016 * tint

    dark = {
        "BG": _make(0.196 + s, cd, hue),
        "SURFACE": _make(0.244 + s * 0.9, cd * 1.3, hue),
        "SURFACE_ALT": _make(0.287 + s * 0.9, cd * 1.3, hue),
        "BORDER": _make(0.352 + s * 0.7, cd * 1.8, hue),
        "HOVER": _make(0.284 + s * 0.9, cd * 1.4, hue),
        "BUBBLE_BOT": _make(0.261 + s * 0.9, cd * 1.3, hue),
        "TEXT": _make(0.955, cd * 0.5, hue),
        "MUTED": _make(0.716, cd * 1.5, hue),
    }
    light = {
        "BG": _make(0.960, cl, hue),
        "SURFACE": "#FFFFFF",
        "SURFACE_ALT": _make(0.933, cl * 1.6, hue),
        "BORDER": _make(0.892, cl * 2.0, hue),
        "HOVER": _make(0.925, cl * 1.9, hue),
        "BUBBLE_BOT": _make(0.924, cl * 1.8, hue),
        "TEXT": _make(0.240, 0.027 * tint * 2, hue),
        "MUTED": _make(0.579, 0.028 * tint * 2, hue),
    }

    # The small gray text has to read on the darkest of the light surfaces too
    light["MUTED"] = _readable(light["MUTED"], light["SURFACE_ALT"], 4.6)

    # The accent: pale on the dark theme (a button with dark text), deep on the light one (a button with white text)
    dark_chroma = 0.0 if neutral else min(chroma * 0.26, 0.05)
    light_l = 0.32 if neutral else 0.437
    dark["ACCENT"] = _make(0.912, dark_chroma, hue)
    dark["ACCENT_HOVER"] = _make(0.855, dark_chroma * 1.3, hue)
    dark["ACCENT_TEXT"] = _make(0.230, 0.0 if neutral else 0.031, hue)
    light["ACCENT"] = _make(light_l, chroma, hue)
    light["ACCENT_HOVER"] = _make(light_l - 0.059, chroma * 0.86, hue)
    light["ACCENT_TEXT"] = "#FFFFFF"
    light["ACCENT"] = _readable(light["ACCENT"], light["ACCENT_TEXT"], 5.0)
    light["ACCENT_HOVER"] = _readable(light["ACCENT_HOVER"], light["ACCENT_TEXT"], 5.5)
    dark["ACCENT"] = _readable(dark["ACCENT"], dark["ACCENT_TEXT"], 8.0)

    # A tinted fill for the open page in the menu, the chosen row and the like
    dark["ACCENT_SOFT"] = _make(0.30 + s * 0.8, 0.0 if neutral else min(chroma * 0.55, 0.07), hue)
    light["ACCENT_SOFT"] = _make(0.915, 0.0 if neutral else min(chroma * 0.30, 0.032), hue)
    # The bright accent: the line under the open page, sliders, progress, focus, the orb
    dark["ACCENT_VIVID"] = _make(0.70, 0.0 if neutral else min(chroma * 1.05, MAX_CHROMA), hue) if not neutral \
        else "#C9C9CD"
    light["ACCENT_VIVID"] = _make(0.56, min(chroma * 1.1, MAX_CHROMA), hue) if not neutral else "#55555B"

    # Bubbles of the user are the accent buttons
    dark["BUBBLE_USER"], dark["BUBBLE_USER_TEXT"] = dark["ACCENT"], dark["ACCENT_TEXT"]
    light["BUBBLE_USER"], light["BUBBLE_USER_TEXT"] = light["ACCENT"], light["ACCENT_TEXT"]

    return {name: (light[name], dark[name]) for name in light}


# Colors that do not depend on the accent: states and services
STATUS_COLORS = {
    "OK": ("#1E9E68", "#3DDC97"),
    "WARN": ("#C98A12", "#F5B63D"),
    "ERROR": ("#C0392B", "#F2677A"),
    "OFF": ("#A99793", "#7B6764"),
    "VIOLET": ("#6D4FD0", "#A58BFF"),
}

# Pairs the text has to be readable on: (foreground, background, the least contrast)
READABILITY = (
    ("TEXT", "BG", 7.0), ("TEXT", "SURFACE", 7.0), ("TEXT", "SURFACE_ALT", 7.0), ("TEXT", "BUBBLE_BOT", 7.0),
    ("MUTED", "BG", 4.5), ("MUTED", "SURFACE", 4.5), ("MUTED", "SURFACE_ALT", 4.5),
    ("ACCENT_TEXT", "ACCENT", 4.5), ("ACCENT_TEXT", "ACCENT_HOVER", 4.5),
    ("TEXT", "ACCENT_SOFT", 7.0), ("ACCENT_VIVID", "BG", 3.0), ("ACCENT_VIVID", "SURFACE", 3.0),
)


def check_scheme(scheme):
    """The pairs of the scheme that are hard to read: [(theme, foreground, background, contrast, needed)]."""
    problems = []
    for index, mode in enumerate(("light", "dark")):
        for foreground, background, needed in READABILITY:
            ratio = contrast_ratio(scheme[foreground][index], scheme[background][index])
            if ratio < needed:
                problems.append((mode, foreground, background, round(ratio, 2), needed))
    return problems
