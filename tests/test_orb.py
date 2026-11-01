"""The orb that shows the state of the assistant: the pictures are made without a screen, so they are checked here."""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.interface import orb  # noqa: E402
from src.interface import palette  # noqa: E402
from src.interface import theme  # noqa: E402
from src.interface.preferences import Preferences  # noqa: E402

SIZE = 96


class FramesTests(unittest.TestCase):
    def frame(self, state="idle", phase=0.0, dark=True):
        return orb.render_frame(SIZE, phase, state, orb.colors_for(state, dark), dark)

    def test_every_state_gives_a_square_rgba_picture_with_the_ball_in_the_middle(self):
        for state in list(orb.STATE_STYLES) + ["something-else"]:
            for dark in (True, False):
                frame = self.frame(state, 0.3, dark)
                self.assertEqual(frame.mode, "RGBA")
                self.assertEqual(frame.size, (SIZE, SIZE))
                middle = frame.getpixel((SIZE // 2, SIZE // 2))[3]
                # The ball that is switched off is a little see-through
                self.assertEqual(middle, 198 if state == "paused" else 255, (state, dark))

    def test_corners_and_edges_are_transparent_so_no_square_is_seen_on_the_background(self):
        for state in orb.STATE_STYLES:
            for phase in (0.0, 0.5):
                alpha = np.asarray(self.frame(state, phase))[..., 3]
                self.assertEqual(int(alpha[0, 0]), 0)
                self.assertEqual(int(alpha[-1, -1]), 0)
                border = np.concatenate([alpha[0], alpha[-1], alpha[:, 0], alpha[:, -1]])
                self.assertLessEqual(int(border.max()), 3, (state, phase))

    def test_the_loop_is_closed(self):
        """The last picture is followed by the first one: they must be the same one or the ball would jump."""
        for state, style in orb.STATE_STYLES.items():
            if style.frames <= 1:
                continue
            start = np.asarray(self.frame(state, 0.0)).astype(int)
            end = np.asarray(self.frame(state, 1.0)).astype(int)
            self.assertLessEqual(int(np.abs(start - end).max()), 2, state)

    def test_the_ball_moves_in_the_states_that_move(self):
        for state, style in orb.STATE_STYLES.items():
            if style.frames <= 1:
                continue
            first = np.asarray(self.frame(state, 0.0)).astype(int)
            later = np.asarray(self.frame(state, 0.5)).astype(int)
            self.assertGreater(int(np.abs(first - later).max()), 8, state)

    def test_paused_ball_is_gray_and_the_others_have_color(self):
        def spread(frame):
            data = np.asarray(frame).astype(int)
            ball = data[SIZE // 2 - 12: SIZE // 2 + 12, SIZE // 2 - 12: SIZE // 2 + 12, :3]
            return int((ball.max(axis=-1) - ball.min(axis=-1)).mean())

        self.assertLess(spread(self.frame("paused")), 14)
        self.assertGreater(spread(self.frame("idle")), 30)

    def test_the_ball_takes_the_color_of_the_accent(self):
        with tempfile.TemporaryDirectory() as folder:
            preferences = Preferences(Path(folder) / "ui.json")
            try:
                hues = {}
                for accent in ("blue", "emerald", "bordo"):
                    preferences.update(accent=accent)
                    theme.apply(preferences)
                    hues[accent] = palette.hex_to_oklch(orb.colors_for("idle", True).first)[2]
            finally:
                theme.apply()
        self.assertLess(abs(hues["blue"] - 258), 15)
        self.assertLess(abs(hues["emerald"] - 160), 15)
        self.assertLess(abs(hues["bordo"] - 27), 15)

    def test_colors_of_the_ring_follow_the_state(self):
        rings = {state: orb.colors_for(state, True).ring for state in orb.STATE_STYLES}
        self.assertEqual(len({rings["listening"], rings["processing"], rings["speaking"], rings["paused"]}), 4)
        for color in rings.values():
            self.assertTrue(palette.is_hex_color(color))

    def test_loop_has_as_many_pictures_as_the_style_says(self):
        colors = orb.colors_for("listening", True)
        loop = orb.render_loop(40, "listening", colors, True)
        self.assertEqual(len(loop), orb.STATE_STYLES["listening"].frames)
        self.assertEqual(len(orb.render_loop(40, "paused", orb.colors_for("paused", True), True)), 1)


class CacheTests(unittest.TestCase):
    def setUp(self):
        orb.clear_loops()
        self.addCleanup(orb.clear_loops)

    def test_loop_is_found_by_its_key(self):
        key = orb.loop_key(10, "idle", orb.colors_for("idle", True), True)
        self.assertIsNone(orb.cached_loop(key))
        frames = [Image.new("RGBA", (10, 10))]
        orb.remember_loop(key, frames)
        self.assertIs(orb.cached_loop(key), frames)

    def test_memory_is_limited_and_the_oldest_loop_goes_first(self):
        big = Image.new("RGBA", (1000, 1000))
        loops = {name: [big] * 10 for name in ("a", "b", "c", "d")}      # 40 MB each as it is counted
        for name, frames in loops.items():
            orb.remember_loop(name, frames)
        self.assertIsNone(orb.cached_loop("a"))
        self.assertIsNotNone(orb.cached_loop("d"))
        total = sum(orb.loop_bytes(orb.cached_loop(name)) for name in "bcd" if orb.cached_loop(name))
        self.assertLessEqual(total, orb._LOOPS_BUDGET)

    def test_the_only_loop_is_kept_even_if_it_is_bigger_than_the_budget(self):
        huge = [Image.new("RGBA", (1000, 1000))] * 40
        orb.remember_loop("huge", huge)
        self.assertIs(orb.cached_loop("huge"), huge)


if __name__ == "__main__":
    unittest.main()
