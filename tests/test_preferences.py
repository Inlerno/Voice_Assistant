"""Settings of the look: they are saved, read back and always checked."""
import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.interface import palette, theme  # noqa: E402
from src.interface import preferences as P  # noqa: E402


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "data" / "ui.json"

    def test_defaults_without_a_file(self):
        prefs = P.Preferences(self.path)
        self.assertEqual(prefs.as_dict(), P.DEFAULTS)
        self.assertFalse(self.path.exists())

    def test_change_is_saved_and_read_back(self):
        prefs = P.Preferences(self.path)
        changed = prefs.update(accent="ocean", scale=1.25, nav="top", user_name="  Аня   ", window=[1000, 700, 10, 20])
        self.assertEqual(changed, {"accent", "scale", "nav", "user_name", "window"})
        again = P.Preferences(self.path)
        self.assertEqual((again.accent, again.scale, again.nav), ("ocean", 1.25, "top"))
        self.assertEqual(again.user_name, "Аня")
        self.assertEqual(again.window, [1000, 700, 10, 20])

    def test_update_reports_only_what_really_changed_and_does_not_write_for_nothing(self):
        prefs = P.Preferences(self.path)
        self.assertEqual(prefs.update(accent="bordo", scale=1.0), set())
        self.assertFalse(self.path.exists())
        self.assertEqual(prefs.update(scale=1.2), {"scale"})
        self.assertEqual(prefs.update(scale=1.2), set())

    def test_unknown_setting_is_an_error(self):
        with self.assertRaises(KeyError):
            P.Preferences(self.path).update(colour="red")

    def test_values_are_limited_to_their_ranges(self):
        prefs = P.Preferences(self.path)
        prefs.update(scale=9, radius=-4, opacity=0.1, wallpaper_dim=5, wallpaper_blur=-1, tint=3, depth=-8)
        self.assertEqual(prefs.scale, P.SCALE_RANGE[1])
        self.assertEqual(prefs.radius, P.RADIUS_RANGE[0])
        self.assertEqual(prefs.opacity, P.OPACITY_RANGE[0])
        self.assertEqual(prefs.wallpaper_dim, P.DIM_RANGE[1])
        self.assertEqual(prefs.wallpaper_blur, P.BLUR_RANGE[0])
        self.assertEqual(prefs.tint, palette.TINT_RANGE[1])
        self.assertEqual(prefs.depth, palette.DEPTH_RANGE[0])

    def test_wrong_values_fall_back_to_the_defaults(self):
        prefs = P.Preferences(self.path)
        prefs.update(nav="diagonal", hero=5, background=None, accent="not a color", font="", show_chips="yes",
                     scale="big", opacity=float("nan"), window=[1, 2, 3], user_name=7)
        for name in ("nav", "hero", "background", "accent", "font", "show_chips", "scale", "opacity", "user_name"):
            self.assertEqual(getattr(prefs, name), P.DEFAULTS[name], name)
        self.assertIsNone(prefs.window)

    def test_the_live_text_of_speech_is_on_by_default_and_is_a_switch(self):
        prefs = P.Preferences(self.path)
        self.assertIs(prefs.live_speech, True)
        self.assertEqual(prefs.update(live_speech=False), {"live_speech"})
        self.assertIs(P.Preferences(self.path).live_speech, False)
        # anything but a real switch is replaced by the default (on)
        self.assertEqual(prefs.update(live_speech="no"), {"live_speech"})
        self.assertIs(prefs.live_speech, True)

    def test_any_color_can_be_the_accent(self):
        prefs = P.Preferences(self.path)
        prefs.update(accent="#3a7bd5")
        self.assertEqual(prefs.accent, "#3A7BD5")
        prefs.update(accent="fff")
        self.assertEqual(prefs.accent, "#FFFFFF")
        self.assertEqual(set(prefs.scheme()), set(palette.build_scheme(palette.accent_family("blue"))))

    def test_name_is_cleaned_and_cut(self):
        prefs = P.Preferences(self.path)
        prefs.update(user_name="a" * 100)
        self.assertEqual(len(prefs.user_name), P.NAME_LENGTH)
        prefs.update(user_name="Иван\n  Петров")
        self.assertEqual(prefs.user_name, "Иван Петров")

    def test_damaged_files_do_not_stop_the_start(self):
        logging.disable(logging.CRITICAL)        # the damaged file is reported to the log: not needed in the output
        self.addCleanup(logging.disable, logging.NOTSET)
        self.path.parent.mkdir(parents=True)
        for text in ("{ not json", "[1, 2]", "null", json.dumps({"accent": 5, "scale": "x", "nav": "top"})):
            self.path.write_text(text, encoding="utf-8")
            prefs = P.Preferences(self.path)
            self.assertIn(prefs.nav, P.NAV_CHOICES)
            self.assertEqual(prefs.accent, P.DEFAULTS["accent"])
            self.assertEqual(prefs.scale, P.DEFAULTS["scale"])
        self.assertEqual(P.Preferences(self.path).nav, "top")

    def test_unknown_names_in_the_file_are_ignored(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"future_setting": 1, "hero": "logo"}), encoding="utf-8")
        prefs = P.Preferences(self.path)
        self.assertEqual(prefs.hero, "logo")
        self.assertFalse(hasattr(prefs, "future_setting"))

    def test_reset_appearance_keeps_the_window(self):
        prefs = P.Preferences(self.path)
        prefs.update(accent="violet", radius=0.2, nav="top", hero="minimal", window=[900, 600, 0, 0],
                     remember_window=False)
        prefs.reset_appearance()
        for name in P.APPEARANCE_KEYS:
            self.assertEqual(getattr(prefs, name), P.DEFAULTS[name], name)
        self.assertEqual(prefs.window, [900, 600, 0, 0])
        self.assertFalse(prefs.remember_window)

    def test_wallpaper_lies_next_to_the_settings(self):
        prefs = P.Preferences(self.path)
        self.assertEqual(prefs.wallpaper_path.parent, self.path.parent)
        self.assertFalse(prefs.has_wallpaper())
        prefs.wallpaper_path.parent.mkdir(parents=True)
        prefs.wallpaper_path.write_bytes(b"x")
        self.assertTrue(prefs.has_wallpaper())


class ThemeApplyTests(unittest.TestCase):
    def tearDown(self):
        theme.apply()

    def test_apply_changes_the_names_the_pages_read(self):
        with tempfile.TemporaryDirectory() as folder:
            prefs = P.Preferences(Path(folder) / "ui.json")
            before = theme.ACCENT
            prefs.update(accent="emerald", font="Arial", radius=0.0)
            theme.apply(prefs)
            self.assertNotEqual(theme.ACCENT, before)
            self.assertEqual(theme.FONT, "Arial")
            self.assertEqual(theme.r(18), 0)
            self.assertEqual(theme.pill(40), 0)
            theme.apply()
            self.assertEqual(theme.ACCENT, before)
            self.assertEqual(theme.r(18), 18)
            self.assertEqual(theme.pill(40), 20)

    def test_corners_never_exceed_half_of_the_height(self):
        with tempfile.TemporaryDirectory() as folder:
            prefs = P.Preferences(Path(folder) / "ui.json")
            prefs.update(radius=1.5)
            theme.apply(prefs)
            self.assertEqual(theme.pill(40), 20)
            self.assertEqual(theme.r(30, limit=10), 10)

    def test_status_colors_are_the_same_in_every_scheme(self):
        with tempfile.TemporaryDirectory() as folder:
            prefs = P.Preferences(Path(folder) / "ui.json")
            ok = theme.OK
            prefs.update(accent="violet")
            theme.apply(prefs)
            self.assertEqual(theme.OK, ok)
            self.assertEqual(theme.MIC_STATUS_COLORS["ok"], ok)


if __name__ == "__main__":
    unittest.main()
