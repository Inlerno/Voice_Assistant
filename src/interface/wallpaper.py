"""The picture behind the welcome screen: it is chosen by the user, kept in a size that is enough for any screen
and shown blurred and darkened (or lightened on the light theme) so the text above it stays readable."""
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

from src.interface import palette

# A picture is cut to this size when it is added: bigger ones only take memory and time
MAX_SIZE = (2560, 1440)
SAVE_QUALITY = 90
# Blurring is done on a smaller copy, it looks the same and takes a fraction of the time
BLUR_WORK_WIDTH = 960


class WallpaperError(Exception):
    """The picture could not be added; the message tells why, in words of the user."""


def import_image(source, target):
    """Reads any picture Pillow knows, makes it smaller if needed and saves it as the wallpaper."""
    try:
        with Image.open(source) as opened:
            picture = ImageOps.exif_transpose(opened).convert("RGB")
    except FileNotFoundError:
        raise WallpaperError("Файл не найден.") from None
    except Exception:
        raise WallpaperError("Не получилось открыть картинку. Подойдут JPG, PNG, WEBP и BMP.") from None
    picture.thumbnail(MAX_SIZE, Image.LANCZOS)
    target = Path(target)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        picture.save(target, "JPEG", quality=SAVE_QUALITY)
    except OSError as error:
        raise WallpaperError(f"Не получилось сохранить картинку: {error}") from None
    render.cache_clear()
    return picture.size


def remove(target):
    try:
        Path(target).unlink()
    except FileNotFoundError:
        pass
    render.cache_clear()


@lru_cache(maxsize=3)
def _load(path, modified):
    with Image.open(path) as opened:
        return opened.convert("RGB")


@lru_cache(maxsize=4)
def render(path, modified, width, height, dim, blur, base):
    """The wallpaper for a canvas of width x height: cut to fit, blurred and mixed with the background color
    (base, #RRGGBB) by the share dim; None if the file cannot be read."""
    try:
        picture = _load(path, modified)
    except Exception:
        return None
    width, height = max(1, int(width)), max(1, int(height))
    picture = ImageOps.fit(picture, (width, height), Image.BICUBIC)
    if blur > 0.4:
        work = min(1.0, BLUR_WORK_WIDTH / width)
        small = picture.resize((max(1, int(width * work)), max(1, int(height * work))), Image.BILINEAR)
        small = small.filter(ImageFilter.GaussianBlur(blur * work))
        picture = small.resize((width, height), Image.BICUBIC)
    if dim > 0:
        cover = Image.new("RGB", picture.size, palette.hex_to_rgb(base))
        picture = Image.blend(picture, cover, min(max(dim, 0.0), 1.0))
    return picture


def fade_into(picture, base, start, end):
    """The picture dissolves into the flat color base (#RRGGBB) between the lines start and end (pixels from the
    top): above start it is untouched, under end there is only the color."""
    import numpy as np

    width, height = picture.size
    rows = np.arange(height, dtype=np.float32)
    span = max(end - start, 1.0)
    edge = np.clip((rows - start) / span, 0.0, 1.0)
    keep = 1.0 - edge * edge * (3.0 - 2.0 * edge)
    flat = np.array(palette.hex_to_rgb(base), dtype=np.float32)
    data = np.asarray(picture, dtype=np.float32)
    blended = data * keep[:, None, None] + flat * (1.0 - keep)[:, None, None]
    return Image.fromarray(np.clip(blended + 0.5, 0, 255).astype(np.uint8), "RGB")


def render_file(path, width, height, dim, blur, base):
    """render() for a file on the disk; a changed file is read again."""
    path = Path(path)
    try:
        modified = path.stat().st_mtime_ns
    except OSError:
        return None
    return render(str(path), modified, width, height, round(dim, 2), round(blur, 1), base)
