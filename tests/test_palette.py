"""The generator of color schemes: conversions, readability of every scheme and the original look."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.interface import palette as P  # noqa: E402

ORIGINAL = {  # the colors of the first design of Astra: (light, dark)
    "BG": ("#F7F0EE", "#1C1214"), "SURFACE": ("#FFFFFF", "#2A1C1D"), "BORDER": ("#E6D8D4", "#4A3532"),
    "TEXT": ("#2B1A1A", "#F6EEEC"), "ACCENT": ("#7D3B35", "#F1DDD6"), "ACCENT_TEXT": ("#FFFFFF", "#2A1717"),
}


class ConversionTests(unittest.TestCase):
    def test_hex_helpers(self):
        self.assertEqual(P.hex_to_rgb("#0a1B2c"), (10, 27, 44))
        self.assertEqual(P.hex_to_rgb("abc"), (170, 187, 204))
        self.assertEqual(P.rgb_to_hex((255.4, -3, 300)), "#FF00FF")
        self.assertTrue(P.is_hex_color("#7D3B35"))
        self.assertTrue(P.is_hex_color("fff"))
        for bad in ("", "#12", "#GGGGGG", "red", None, 5, "#1234567"):
            self.assertFalse(P.is_hex_color(bad), bad)
        self.assertEqual(P.normalize_hex(" #7d3b35 "), "#7D3B35")
        self.assertEqual(P.normalize_hex("nonsense"), "#7D3B35")
        self.assertEqual(P.normalize_hex("nonsense", "#000000"), "#000000")

    def test_oklch_round_trip(self):
        for color in ("#7D3B35", "#3E6DD0", "#F1DDD6", "#101010", "#FFFFFF", "#00FF00"):
            lightness, chroma, hue = P.hex_to_oklch(color)
            back = P.oklch_to_hex(lightness, chroma, hue)
            for a, b in zip(P.hex_to_rgb(color), P.hex_to_rgb(back)):
                self.assertLessEqual(abs(a - b), 2, (color, back))

    def test_black_and_white(self):
        self.assertAlmostEqual(P.hex_to_oklch("#FFFFFF")[0], 1.0, places=2)
        self.assertAlmostEqual(P.hex_to_oklch("#000000")[0], 0.0, places=2)
        self.assertEqual(P.oklch_to_hex(0.0, 0.2, 100), "#000000")
        self.assertEqual(P.oklch_to_hex(1.0, 0.2, 100), "#FFFFFF")

    def test_colors_outside_the_screen_are_pulled_in(self):
        color = P.oklch_to_hex(0.5, 0.4, 145)   # no such green on a screen
        self.assertTrue(P.is_hex_color(color))
        _, chroma, hue = P.hex_to_oklch(color)
        self.assertLess(chroma, 0.4)
        self.assertAlmostEqual(hue, 145, delta=8)

    def test_contrast(self):
        self.assertAlmostEqual(P.contrast_ratio("#000000", "#FFFFFF"), 21.0, places=1)
        self.assertAlmostEqual(P.contrast_ratio("#777777", "#777777"), 1.0, places=3)
        self.assertEqual(P.contrast_ratio("#123456", "#ABCDEF"), P.contrast_ratio("#ABCDEF", "#123456"))

    def test_mix(self):
        self.assertEqual(P.mix("#000000", "#FFFFFF", 0), "#000000")
        self.assertEqual(P.mix("#000000", "#FFFFFF", 1), "#FFFFFF")
        middle = P.hex_to_oklch(P.mix("#000000", "#FFFFFF", 0.5))[0]
        self.assertAlmostEqual(middle, 0.5, delta=0.02)


class AccentTests(unittest.TestCase):
    def test_presets(self):
        self.assertEqual(len(P.PRESETS), len({key for key, *_ in P.PRESETS}))
        self.assertEqual(P.PRESETS[0][0], P.DEFAULT_PRESET)
        self.assertEqual(P.accent_family("ocean"), P.PRESET_FAMILIES["ocean"])
        self.assertEqual(set(P.PRESET_TITLES), set(P.PRESET_FAMILIES))

    def test_family_from_a_color(self):
        family = P.accent_family("#2563EB")
        self.assertAlmostEqual(family.hue, 262, delta=6)
        self.assertGreater(family.chroma, 0.15)
        self.assertFalse(family.neutral)

    def test_gray_is_neutral(self):
        self.assertTrue(P.accent_family("#808080").neutral)
        self.assertTrue(P.accent_family("graphite").neutral)

    def test_unknown_value_gives_the_default(self):
        self.assertEqual(P.accent_family("what"), P.PRESET_FAMILIES[P.DEFAULT_PRESET])
        self.assertEqual(P.accent_family(None), P.PRESET_FAMILIES[P.DEFAULT_PRESET])

    def test_chroma_is_limited(self):
        self.assertLessEqual(P.accent_family("#FF0000").chroma, P.MAX_CHROMA)

    def test_swatch_is_the_accent_of_the_light_theme(self):
        self.assertEqual(P.accent_swatch("bordo"), "#7D3B35")


class SchemeTests(unittest.TestCase):
    def test_every_token_is_a_pair_of_colors(self):
        scheme = P.build_scheme(P.accent_family("ocean"))
        for name in ("BG", "SURFACE", "SURFACE_ALT", "BORDER", "TEXT", "MUTED", "HOVER", "ACCENT", "ACCENT_TEXT",
                     "ACCENT_HOVER", "ACCENT_SOFT", "ACCENT_VIVID", "BUBBLE_USER", "BUBBLE_USER_TEXT", "BUBBLE_BOT"):
            self.assertIn(name, scheme)
            self.assertEqual(len(scheme[name]), 2)
            self.assertTrue(all(P.is_hex_color(c) for c in scheme[name]), name)

    def test_original_look_is_kept_by_the_default(self):
        scheme = P.build_scheme(P.accent_family(P.DEFAULT_PRESET))
        for name, (light, dark) in ORIGINAL.items():
            for mine, original in zip(scheme[name], (light, dark)):
                a, b = P.hex_to_oklch(mine), P.hex_to_oklch(original)
                self.assertAlmostEqual(a[0], b[0], delta=0.02, msg=(name, mine, original))

    def test_every_scheme_is_readable(self):
        # all presets, from plain gray backgrounds to strongly tinted ones, from the deepest to the lightest dark
        for key, *_ in P.PRESETS:
            for tint in (0.0, 0.25, 0.5, 0.75, 1.0):
                for depth in (-1.0, -0.5, 0.0, 0.5, 1.0):
                    scheme = P.build_scheme(P.accent_family(key), tint, depth)
                    self.assertEqual(P.check_scheme(scheme), [], (key, tint, depth))

    def test_any_color_of_the_user_is_readable(self):
        for color in ("#FF0000", "#00FF00", "#0000FF", "#FFFF00", "#FF00FF", "#00FFFF", "#FFFFFF", "#000000",
                      "#808080", "#FFD700", "#123456", "#FEDCBA"):
            for tint in (0.0, 0.5, 1.0):
                scheme = P.build_scheme(P.accent_family(color), tint)
                self.assertEqual(P.check_scheme(scheme), [], (color, tint))

    def test_tint_controls_how_colored_the_backgrounds_are(self):
        family = P.accent_family("blue")
        chromas = [P.hex_to_oklch(P.build_scheme(family, tint)["BG"][1])[1] for tint in (0.0, 0.5, 1.0)]
        self.assertLess(chromas[0], 0.002)
        self.assertLess(chromas[0], chromas[1])
        self.assertLess(chromas[1], chromas[2])

    def test_depth_moves_the_dark_background(self):
        family = P.accent_family("bordo")
        lights = [P.hex_to_oklch(P.build_scheme(family, 0.5, d)["BG"][1])[0] for d in (-1.0, 0.0, 1.0)]
        self.assertLess(lights[0], lights[1])
        self.assertLess(lights[1], lights[2])
        # the light theme does not depend on the depth
        self.assertEqual(P.build_scheme(family, 0.5, -1.0)["BG"][0], P.build_scheme(family, 0.5, 1.0)["BG"][0])

    def test_out_of_range_values_are_limited(self):
        family = P.accent_family("bordo")
        self.assertEqual(P.build_scheme(family, 5.0, 9.0), P.build_scheme(family, 1.0, 1.0))
        self.assertEqual(P.build_scheme(family, -2.0, -9.0), P.build_scheme(family, 0.0, -1.0))

    def test_graphite_is_gray(self):
        scheme = P.build_scheme(P.accent_family("graphite"))
        for name in ("BG", "SURFACE_ALT", "ACCENT"):
            for color in scheme[name]:
                self.assertLess(P.hex_to_oklch(color)[1], 0.012, (name, color))

    def test_the_soft_accent_can_be_told_from_the_background(self):
        scheme = P.build_scheme(P.accent_family("violet"))
        for index in (0, 1):
            self.assertGreater(P.contrast_ratio(scheme["ACCENT_SOFT"][index], scheme["BG"][index]), 1.05)


if __name__ == "__main__":
    unittest.main()
