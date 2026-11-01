"""The picture behind the home page: adding it, fitting it to the window, dissolving it into the page."""
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.interface import wallpaper  # noqa: E402

BASE = "#201818"
BASE_RGB = (0x20, 0x18, 0x18)


class WallpaperTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.addCleanup(wallpaper.render.cache_clear)
        self.dir = Path(self.folder.name)
        wallpaper.render.cache_clear()

    def picture(self, name="source.png", size=(800, 450), color=(200, 90, 40)):
        path = self.dir / name
        Image.new("RGB", size, color).save(path)
        return path

    def test_import_keeps_a_small_picture_as_it_is(self):
        target = self.dir / "data" / "wallpaper.jpg"
        size = wallpaper.import_image(self.picture(), target)
        self.assertEqual(size, (800, 450))
        self.assertTrue(target.exists())
        with Image.open(target) as saved:
            self.assertEqual(saved.format, "JPEG")

    def test_import_makes_a_big_picture_smaller_and_keeps_its_shape(self):
        target = self.dir / "wallpaper.jpg"
        size = wallpaper.import_image(self.picture(size=(5120, 2880)), target)
        self.assertLessEqual(size[0], wallpaper.MAX_SIZE[0])
        self.assertLessEqual(size[1], wallpaper.MAX_SIZE[1])
        self.assertAlmostEqual(size[0] / size[1], 16 / 9, places=2)

    def test_import_takes_a_picture_with_transparency(self):
        source = self.dir / "clear.png"
        Image.new("RGBA", (64, 64), (255, 0, 0, 40)).save(source)
        self.assertEqual(wallpaper.import_image(source, self.dir / "w.jpg"), (64, 64))

    def test_a_missing_file_is_explained(self):
        with self.assertRaises(wallpaper.WallpaperError) as caught:
            wallpaper.import_image(self.dir / "nothing.png", self.dir / "w.jpg")
        self.assertIn("не найден", str(caught.exception))

    def test_a_file_that_is_not_a_picture_is_explained_and_nothing_is_saved(self):
        text = self.dir / "text.png"
        text.write_text("это не картинка")
        target = self.dir / "w.jpg"
        with self.assertRaises(wallpaper.WallpaperError) as caught:
            wallpaper.import_image(text, target)
        self.assertIn("JPG", str(caught.exception))
        self.assertFalse(target.exists())

    def test_remove_deletes_the_file_and_is_quiet_when_there_is_none(self):
        target = self.dir / "w.jpg"
        wallpaper.import_image(self.picture(), target)
        wallpaper.remove(target)
        self.assertFalse(target.exists())
        wallpaper.remove(target)

    def test_render_fits_the_picture_to_the_window(self):
        target = self.dir / "w.jpg"
        wallpaper.import_image(self.picture(), target)
        for width, height in [(1200, 700), (300, 900), (1, 1)]:
            shown = wallpaper.render_file(target, width, height, 0.0, 0.0, BASE)
            self.assertEqual(shown.size, (width, height))

    def test_dim_mixes_the_picture_with_the_color_of_the_page(self):
        target = self.dir / "w.jpg"
        wallpaper.import_image(self.picture(color=(250, 250, 250)), target)
        clear = wallpaper.render_file(target, 64, 64, 0.0, 0.0, BASE).getpixel((32, 32))
        half = wallpaper.render_file(target, 64, 64, 0.5, 0.0, BASE).getpixel((32, 32))
        full = wallpaper.render_file(target, 64, 64, 1.0, 0.0, BASE).getpixel((32, 32))
        self.assertGreater(clear[0], half[0])
        self.assertGreater(half[0], full[0])
        for got, want in zip(full, BASE_RGB):
            self.assertAlmostEqual(got, want, delta=1)

    def test_blur_softens_edges(self):
        source = self.dir / "stripes.png"
        stripes = Image.new("RGB", (200, 200), (0, 0, 0))
        for x in range(100, 200):
            for y in range(200):
                stripes.putpixel((x, y), (255, 255, 255))
        stripes.save(source)
        target = self.dir / "w.jpg"
        wallpaper.import_image(source, target)
        sharp = wallpaper.render_file(target, 200, 200, 0.0, 0.0, BASE)
        soft = wallpaper.render_file(target, 200, 200, 0.0, 8.0, BASE)
        self.assertLess(sharp.getpixel((90, 100))[0], 40)
        self.assertGreater(soft.getpixel((90, 100))[0], sharp.getpixel((90, 100))[0] + 20)

    def test_a_changed_file_is_read_again(self):
        target = self.dir / "w.jpg"
        wallpaper.import_image(self.picture(color=(255, 0, 0)), target)
        first = wallpaper.render_file(target, 32, 32, 0.0, 0.0, BASE).getpixel((16, 16))
        wallpaper.import_image(self.picture("other.png", color=(0, 0, 255)), target)
        second = wallpaper.render_file(target, 32, 32, 0.0, 0.0, BASE).getpixel((16, 16))
        self.assertGreater(first[0], first[2])
        self.assertGreater(second[2], second[0])

    def test_an_unreadable_file_gives_nothing(self):
        self.assertIsNone(wallpaper.render_file(self.dir / "nothing.jpg", 100, 100, 0.0, 0.0, BASE))
        broken = self.dir / "broken.jpg"
        broken.write_bytes(b"not a jpeg")
        self.assertIsNone(wallpaper.render_file(broken, 100, 100, 0.0, 0.0, BASE))


class FadeTests(unittest.TestCase):
    def setUp(self):
        self.picture = Image.new("RGB", (40, 200), (255, 255, 255))

    def test_above_the_start_the_picture_is_untouched_and_under_the_end_it_is_gone(self):
        faded = wallpaper.fade_into(self.picture, BASE, 60, 140)
        self.assertEqual(faded.size, self.picture.size)
        self.assertEqual(faded.getpixel((5, 0)), (255, 255, 255))
        self.assertEqual(faded.getpixel((5, 60)), (255, 255, 255))
        self.assertEqual(faded.getpixel((5, 140)), BASE_RGB)
        self.assertEqual(faded.getpixel((5, 199)), BASE_RGB)

    def test_in_between_it_fades_smoothly(self):
        faded = wallpaper.fade_into(self.picture, BASE, 60, 140)
        values = [faded.getpixel((5, row))[0] for row in range(60, 141)]
        self.assertEqual(values, sorted(values, reverse=True))
        self.assertLess(abs(values[40] - (255 + BASE_RGB[0]) / 2), 3)
        steps = [a - b for a, b in zip(values, values[1:])]
        self.assertLess(max(steps), 8)

    def test_a_line_instead_of_a_range_does_not_break(self):
        faded = wallpaper.fade_into(self.picture, BASE, 100, 100)
        self.assertEqual(faded.getpixel((5, 10)), (255, 255, 255))
        self.assertEqual(faded.getpixel((5, 190)), BASE_RGB)

    def test_the_picture_that_was_given_is_not_changed(self):
        wallpaper.fade_into(self.picture, BASE, 0, 100)
        self.assertEqual(self.picture.getpixel((5, 150)), (255, 255, 255))


if __name__ == "__main__":
    unittest.main()
