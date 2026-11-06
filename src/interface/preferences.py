"""Settings of the look and the window that the user can change; they are kept in data/ui.json.

Everything read from the file is checked: a value of a wrong type or out of range is replaced by the default,
so a hand-edited or damaged file never stops the interface from starting.
"""
import json
import logging
from pathlib import Path

from src.interface import palette
from src.productivity.planner import DATA_DIR

logger = logging.getLogger(__name__)

SETTINGS_FILE = DATA_DIR / "ui.json"
WALLPAPER_FILE = DATA_DIR / "wallpaper.jpg"

NAV_CHOICES = ("left", "top")
HERO_CHOICES = ("orb", "logo", "minimal")
BACKGROUND_CHOICES = ("glow", "flat", "image")

SCALE_RANGE = (0.8, 1.5)
RADIUS_RANGE = (0.0, 1.5)
OPACITY_RANGE = (0.6, 1.0)
DIM_RANGE = (0.0, 0.9)
BLUR_RANGE = (0.0, 24.0)
NAME_LENGTH = 24

# The name of a font that is on every Windows; others are offered from the list of the installed ones
DEFAULT_FONT = "Segoe UI"

DEFAULTS = {
    # colors
    "accent": palette.DEFAULT_PRESET,      # a key of a ready accent or a #RRGGBB color
    "tint": palette.DEFAULT_TINT,
    "depth": 0.0,
    # shapes and text
    "font": DEFAULT_FONT,
    "scale": 1.0,
    "radius": 1.0,
    # the window
    "nav": "left",
    "nav_collapsed": False,
    "opacity": 1.0,
    "always_on_top": False,
    "remember_window": True,
    "window": None,                         # [width, height, x, y] of the last session
    # the home page
    "hero": "orb",
    "background": "glow",
    "wallpaper_dim": 0.5,
    "wallpaper_blur": 6.0,
    "user_name": "",
    "show_chips": True,
    "show_timings": True,
    "live_speech": True,                    # the words of a command are shown while they are being said
    "animations": True,
}

# What can be changed from the appearance page and is reset by the "Reset" button (not the window of the last session)
APPEARANCE_KEYS = tuple(key for key in DEFAULTS if key not in ("window", "remember_window"))


def _number(value, bounds, default):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if value != value:  # NaN
        return default
    return min(max(float(value), bounds[0]), bounds[1])


def _clean(name, value):
    """The value if it is fine for the setting, otherwise the default one."""
    default = DEFAULTS[name]
    if name == "accent":
        if value in palette.PRESET_FAMILIES:
            return value
        return palette.normalize_hex(value, "") if palette.is_hex_color(value) else default
    if name == "tint":
        return _number(value, palette.TINT_RANGE, default)
    if name == "depth":
        return _number(value, palette.DEPTH_RANGE, default)
    if name == "font":
        return value.strip() if isinstance(value, str) and value.strip() and len(value) <= 80 else default
    if name == "scale":
        return round(_number(value, SCALE_RANGE, default), 2)
    if name == "radius":
        return _number(value, RADIUS_RANGE, default)
    if name == "nav":
        return value if value in NAV_CHOICES else default
    if name == "hero":
        return value if value in HERO_CHOICES else default
    if name == "background":
        return value if value in BACKGROUND_CHOICES else default
    if name == "opacity":
        return _number(value, OPACITY_RANGE, default)
    if name == "wallpaper_dim":
        return _number(value, DIM_RANGE, default)
    if name == "wallpaper_blur":
        return _number(value, BLUR_RANGE, default)
    if name == "user_name":
        return " ".join(value.split())[:NAME_LENGTH] if isinstance(value, str) else default
    if name == "window":
        if (isinstance(value, (list, tuple)) and len(value) == 4
                and all(isinstance(v, int) and not isinstance(v, bool) for v in value)
                and value[0] >= 300 and value[1] >= 300):
            return list(value)
        return None
    # the rest are switches
    return value if isinstance(value, bool) else default


class Preferences:
    """The settings; every one is an attribute (prefs.accent) and is saved as soon as it is changed."""

    def __init__(self, path=None):
        self.path = Path(path) if path else SETTINGS_FILE
        for name, value in DEFAULTS.items():
            setattr(self, name, value)
        self._load()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            logger.exception("Не удалось прочитать %s", self.path)
            return
        if not isinstance(data, dict):
            return
        for name in DEFAULTS:
            if name in data:
                setattr(self, name, _clean(name, data[name]))

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            logger.exception("Не удалось сохранить настройки оформления")

    def as_dict(self):
        return {name: getattr(self, name) for name in DEFAULTS}

    def update(self, **changes):
        """Changes the settings and saves them; returns the names of those that really changed."""
        changed = set()
        for name, value in changes.items():
            if name not in DEFAULTS:
                raise KeyError(name)
            value = _clean(name, value)
            if getattr(self, name) != value:
                setattr(self, name, value)
                changed.add(name)
        if changed:
            self.save()
        return changed

    def reset_appearance(self):
        """Back to the original look; the size of the window and the wallpaper file are left as they are."""
        return self.update(**{name: DEFAULTS[name] for name in APPEARANCE_KEYS})

    # ---------- What follows from the settings ----------

    def scheme(self):
        """The palette of the interface for the current accent, tint and depth."""
        return palette.build_scheme(palette.accent_family(self.accent), self.tint, self.depth)

    @property
    def wallpaper_path(self):
        """The processed picture for the home page lies next to the settings file."""
        return self.path.parent / WALLPAPER_FILE.name

    def has_wallpaper(self):
        return self.wallpaper_path.exists()
