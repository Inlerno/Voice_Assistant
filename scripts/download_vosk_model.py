"""Download a big Vosk model into src/assets, where the assistant finds it by itself.

Usage (from the project root):
    uv run python scripts/download_vosk_model.py
    uv run python scripts/download_vosk_model.py vosk-model-ru-0.22

The download can be interrupted and started again: it continues from where it stopped.
"""
import argparse
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

BASE_URL = "https://alphacephei.com/vosk/models/"
DEFAULT_MODEL = "vosk-model-ru-0.42"
ASSETS_DIR = Path(__file__).resolve().parents[1] / "src" / "assets"
CHUNK = 1024 * 1024
# The archive and the unpacked model must fit together
SPACE_FACTOR = 3


def _megabytes(size):
    return f"{size / 1024 / 1024:.0f} МБ"


# Download url to path, continuing a partial file. Returns the size of the file.
def download(url, path: Path, show=print):
    path.parent.mkdir(parents=True, exist_ok=True)
    have = path.stat().st_size if path.exists() else 0
    request = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
    with urllib.request.urlopen(request, timeout=60) as response:
        if response.status == 206:
            total = have + int(response.headers.get("Content-Length", 0))
            mode = "ab"
            show(f"Продолжаю с {_megabytes(have)}")
        else:
            # The server sent the whole file again (or there was nothing to continue)
            total = int(response.headers.get("Content-Length", 0))
            have, mode = 0, "wb"
        free = shutil.disk_usage(path.parent).free
        if total and free < (total - have) * SPACE_FACTOR:
            raise RuntimeError(
                f"Мало места на диске: свободно {_megabytes(free)}, нужно около {_megabytes(total * SPACE_FACTOR)}."
            )
        done = have
        last_percent = -1
        with open(path, mode) as out:
            while True:
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if total:
                    percent = done * 100 // total
                    if percent != last_percent:
                        last_percent = percent
                        show(f"\rСкачано {percent}% ({_megabytes(done)} из {_megabytes(total)})", end="")
        show("")
    if total and path.stat().st_size != total:
        raise RuntimeError("Файл скачан не полностью, запустите скрипт ещё раз, чтобы докачать.")
    return path.stat().st_size


# Unpack the archive into target_dir; the model folder appears only when everything is unpacked
def unpack(archive: Path, target_dir: Path, name: str, show=print):
    staging = target_dir / f"{name}.unpacking"
    if staging.exists():
        shutil.rmtree(staging)
    with zipfile.ZipFile(archive) as zf:
        bad = zf.testzip()
        if bad:
            raise RuntimeError(f"Архив повреждён ({bad}). Удалите {archive.name} и запустите скрипт заново.")
        show("Распаковываю…")
        zf.extractall(staging)
    unpacked = staging / name
    if not unpacked.is_dir():
        shutil.rmtree(staging)
        raise RuntimeError(f"В архиве нет папки {name}.")
    final = target_dir / name
    if final.exists():
        shutil.rmtree(final)
    unpacked.replace(final)
    shutil.rmtree(staging, ignore_errors=True)
    return final


def main(argv=None):
    parser = argparse.ArgumentParser(description="Скачать большую модель Vosk в src/assets")
    parser.add_argument("name", nargs="?", default=DEFAULT_MODEL, help=f"имя модели, по умолчанию {DEFAULT_MODEL}")
    parser.add_argument("--base-url", default=BASE_URL, help="откуда скачивать")
    parser.add_argument("--assets", default=str(ASSETS_DIR), help="куда класть модель")
    args = parser.parse_args(argv)

    assets = Path(args.assets)
    final = assets / args.name
    if (final / "am").is_dir():
        print(f"Модель уже есть: {final}")
        return 0

    archive = assets / f"{args.name}.zip.part"
    url = args.base_url + args.name + ".zip"
    print(f"Скачиваю {url}")
    try:
        download(url, archive, lambda text, end="\n": print(text, end=end, flush=True))
        final = unpack(archive, assets, args.name)
    except Exception as e:
        print(f"\nОшибка: {e}", file=sys.stderr)
        return 1
    archive.unlink(missing_ok=True)
    print(f"Готово: {final}\nПерезапустите ассистента, большая модель загрузится сама.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
