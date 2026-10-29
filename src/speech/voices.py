"""Voices of the assistant: the ones of Windows, the neural ones from the catalog and the ones added by the user.

A neural voice is a Piper model (VITS) run by sherpa-onnx. It lives in its own folder of data/voices:

    data/voices/<id>/model.onnx   the network
    data/voices/<id>/tokens.txt   its alphabet
    data/voices/<id>/voice.json   the name and the details shown in the interface
    data/voices/espeak-ng-data/   the pronunciation rules, shared by all the voices

A voice is added from a ready sherpa-onnx folder or archive, or from the files that Piper publishes
(model.onnx + model.onnx.json): the missing parts are made on the way.
"""
import json
import logging
import re
import shutil
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import requests

from src.productivity.planner import DATA_DIR

logger = logging.getLogger(__name__)

VOICES_DIR = DATA_DIR / "voices"
SETTINGS_FILE = DATA_DIR / "tts.json"
SHARED_DATA = "espeak-ng-data"
SYSTEM_PREFIX = "sapi:"
MODELS_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"
SHARED_DATA_URL = MODELS_URL + "espeak-ng-data.tar.bz2"

# kinds of a voice
SYSTEM, NEURAL = "system", "neural"
# where a voice came from
BUILTIN, CATALOG_SOURCE, CUSTOM = "builtin", "catalog", "custom"


class VoiceError(Exception):
    """A problem that can be shown to the user as it is."""


@dataclass
class Voice:
    id: str
    name: str
    kind: str                      # SYSTEM or NEURAL
    source: str = BUILTIN          # BUILTIN, CATALOG_SOURCE or CUSTOM
    description: str = ""
    path: Path | None = None       # the folder of a neural voice
    speakers: int = 1
    sample_rate: int = 22050

    @property
    def removable(self):
        return self.kind == NEURAL


@dataclass(frozen=True)
class CatalogVoice:
    id: str
    name: str
    description: str
    archive: str
    size_mb: int

    @property
    def url(self):
        return MODELS_URL + self.archive


# Russian voices of the sherpa-onnx catalog (Piper, medium quality)
CATALOG = (
    CatalogVoice("irina", "Ирина", "Женский, мягкий и спокойный", "vits-piper-ru_RU-irina-medium.tar.bz2", 67),
    CatalogVoice("denis", "Денис", "Мужской, ровный", "vits-piper-ru_RU-denis-medium.tar.bz2", 67),
    CatalogVoice("dmitri", "Дмитрий", "Мужской, низкий", "vits-piper-ru_RU-dmitri-medium.tar.bz2", 67),
    CatalogVoice("ruslan", "Руслан", "Мужской, чёткий", "vits-piper-ru_RU-ruslan-medium.tar.bz2", 67),
)


# ---------- Settings ----------

class TtsSettings:
    """Voice, speed and volume; saved to data/tts.json after every change."""

    RATE_RANGE = (0.6, 1.6)
    VOLUME_RANGE = (0.1, 1.0)

    def __init__(self, path=None):
        self.path = Path(path) if path else SETTINGS_FILE
        self.voice = None          # None: the default voice of Windows
        self.rate = 1.0
        self.volume = 1.0
        self.speaker = 0
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
        self.voice = data.get("voice") or None
        self.rate = self._clamp(data.get("rate"), self.RATE_RANGE, 1.0)
        self.volume = self._clamp(data.get("volume"), self.VOLUME_RANGE, 1.0)
        speaker = data.get("speaker")
        self.speaker = speaker if isinstance(speaker, int) and speaker >= 0 else 0

    @staticmethod
    def _clamp(value, bounds, default):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return default
        return min(max(float(value), bounds[0]), bounds[1])

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(
                {"voice": self.voice, "rate": round(self.rate, 2), "volume": round(self.volume, 2),
                 "speaker": self.speaker}, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            logger.exception("Не удалось сохранить настройки озвучки")

    def set_rate(self, value):
        self.rate = self._clamp(value, self.RATE_RANGE, 1.0)
        self.save()

    def set_volume(self, value):
        self.volume = self._clamp(value, self.VOLUME_RANGE, 1.0)
        self.save()

    def set_voice(self, voice_id, speaker=0):
        self.voice = voice_id
        self.speaker = speaker
        self.save()


# ---------- Reading and patching an ONNX file ----------
# An ONNX model is a protobuf message; the metadata (field 14) that sherpa-onnx needs can simply be appended to
# the end of the file, because protobuf joins repeated fields.

def _read_varint(data, pos):
    result = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return result, pos


def _write_varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _walk_fields(data):
    """(number, wire type, payload) of every top level field of a protobuf message."""
    pos = 0
    while pos < len(data):
        tag, pos = _read_varint(data, pos)
        number, wire = tag >> 3, tag & 7
        if wire == 0:
            value, pos = _read_varint(data, pos)
        elif wire == 2:
            length, pos = _read_varint(data, pos)
            value = data[pos:pos + length]
            pos += length
        elif wire == 1:
            value = data[pos:pos + 8]
            pos += 8
        elif wire == 5:
            value = data[pos:pos + 4]
            pos += 4
        else:
            raise ValueError("unsupported wire type")
        yield number, wire, value


def read_onnx_metadata(data):
    """The metadata of an ONNX model as a dict (data is the content of the file)."""
    result = {}
    for number, wire, value in _walk_fields(data):
        if number == 14 and wire == 2:
            entry = {n: v for n, w, v in _walk_fields(value) if w == 2}
            if 1 in entry:
                result[entry[1].decode("utf-8", "replace")] = entry.get(2, b"").decode("utf-8", "replace")
    return result


def add_onnx_metadata(data, items):
    """The content of the model with extra metadata entries (keys that are already there are left alone)."""
    existing = read_onnx_metadata(data)
    extra = bytearray()
    for key, value in items.items():
        if key in existing:
            continue
        key_b, value_b = key.encode("utf-8"), str(value).encode("utf-8")
        entry = b"\x0a" + _write_varint(len(key_b)) + key_b + b"\x12" + _write_varint(len(value_b)) + value_b
        extra += b"\x72" + _write_varint(len(entry)) + entry
    return bytes(data) + bytes(extra)


def looks_like_onnx(data):
    try:
        return any(number == 7 for number, _, _ in _walk_fields(data[:]))
    except (ValueError, IndexError):
        return False


# ---------- The library of neural voices ----------

def slugify(text):
    translit = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
                "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
                "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ы": "y", "э": "e",
                "ю": "yu", "я": "ya"}
    text = "".join(translit.get(ch, ch) for ch in text.lower())
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "voice"


class VoiceLibrary:
    def __init__(self, root=None, downloader=None):
        self.root = Path(root) if root else VOICES_DIR
        # downloader(url, destination, progress, cancel) - replaceable in tests
        self._download = downloader or self._http_download

    # ---------- What is installed ----------

    @property
    def shared_data(self):
        return self.root / SHARED_DATA

    def has_shared_data(self):
        return self.shared_data.is_dir() and any(self.shared_data.iterdir())

    def installed(self):
        voices = []
        if not self.root.exists():
            return voices
        for folder in sorted(self.root.iterdir()):
            info = folder / "voice.json"
            if not (folder.is_dir() and info.exists() and (folder / "model.onnx").exists()
                    and (folder / "tokens.txt").exists()):
                continue
            try:
                details = json.loads(info.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            voices.append(Voice(
                id=folder.name, name=details.get("name") or folder.name, kind=NEURAL,
                source=details.get("source", CUSTOM), description=details.get("description", ""), path=folder,
                speakers=int(details.get("speakers", 1) or 1), sample_rate=int(details.get("sample_rate", 22050)),
            ))
        return voices

    def get(self, voice_id):
        return next((v for v in self.installed() if v.id == voice_id), None)

    def is_installed(self, voice_id):
        return self.get(voice_id) is not None

    def remove(self, voice_id):
        voice = self.get(voice_id)
        if voice is None:
            raise VoiceError("Такого голоса нет.")
        shutil.rmtree(voice.path, ignore_errors=True)
        logger.info("Голос удалён: %s", voice.name)

    # ---------- Download ----------

    @staticmethod
    def _http_download(url, destination, progress=None, cancel=None):
        try:
            with requests.get(url, stream=True, timeout=30) as response:
                response.raise_for_status()
                total = int(response.headers.get("Content-Length") or 0)
                done = 0
                with open(destination, "wb") as out:
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if cancel is not None and cancel.is_set():
                            raise VoiceError("Загрузка отменена.")
                        out.write(chunk)
                        done += len(chunk)
                        if progress and total:
                            progress(min(done / total, 1.0))
        except requests.RequestException as error:
            raise VoiceError(f"Не удалось скачать файл. Проверьте интернет и попробуйте ещё раз. ({error})")

    def _fetch(self, url, work, progress=None, cancel=None):
        """Downloads and unpacks an archive into the work folder; returns the folder with its content."""
        archive = Path(work) / "download.archive"
        self._download(url, archive, progress, cancel)
        target = Path(work) / "unpacked"
        target.mkdir()
        self._unpack(archive, target)
        return self._single_root(target)

    @staticmethod
    def _unpack(archive, target):
        try:
            if zipfile.is_zipfile(archive):
                with zipfile.ZipFile(archive) as zf:
                    for member in zf.namelist():
                        resolved = (Path(target) / member).resolve()
                        if not str(resolved).startswith(str(Path(target).resolve())):
                            raise VoiceError("Архив содержит недопустимые пути.")
                    zf.extractall(target)
            else:
                with tarfile.open(archive) as tf:
                    tf.extractall(target, filter="data")
        except (tarfile.TarError, zipfile.BadZipFile, EOFError, OSError) as error:
            raise VoiceError(f"Не удалось распаковать архив: {error}")

    @staticmethod
    def _single_root(folder):
        entries = [p for p in Path(folder).iterdir()]
        return entries[0] if len(entries) == 1 and entries[0].is_dir() else Path(folder)

    def ensure_shared_data(self, progress=None, source=None, cancel=None):
        """The pronunciation rules (espeak-ng) that every voice needs: taken from a folder or downloaded."""
        if self.has_shared_data():
            return
        self.root.mkdir(parents=True, exist_ok=True)
        if source is not None and Path(source).is_dir():
            shutil.copytree(source, self.shared_data)
            return
        with tempfile.TemporaryDirectory(dir=self.root) as work:
            folder = self._fetch(SHARED_DATA_URL, work, progress, cancel)
            data = folder if folder.name == SHARED_DATA else folder / SHARED_DATA
            if not data.is_dir():
                raise VoiceError("В скачанном архиве нет данных произношения.")
            shutil.move(str(data), str(self.shared_data))

    def download(self, voice_id, progress=None, cancel=None):
        """Downloads a voice of the catalog and installs it."""
        entry = next((c for c in CATALOG if c.id == voice_id), None)
        if entry is None:
            raise VoiceError("Такого голоса нет в каталоге.")
        self.root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.root) as work:
            folder = self._fetch(entry.url, work, progress, cancel)
            shared = folder / SHARED_DATA
            if shared.is_dir() and not self.has_shared_data():
                shutil.move(str(shared), str(self.shared_data))
            voice = self._install_folder(folder, entry.name, CATALOG_SOURCE, entry.description, voice_id=entry.id)
        logger.info("Голос установлен: %s", voice.name)
        return voice

    # ---------- Adding the own voice ----------

    def import_voice(self, path, name=None, progress=None, cancel=None):
        """Adds a voice from a folder, an archive or a .onnx file (see the module description)."""
        source = Path(path)
        if not source.exists():
            raise VoiceError("Файл не найден.")
        self.root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.root) as work:
            if source.is_dir():
                folder = source
            elif source.suffix.lower() == ".onnx":
                folder = source.parent
            else:
                folder = Path(work) / "unpacked"
                folder.mkdir()
                self._unpack(source, folder)
                folder = self._single_root(folder)
            model = self._find_model(folder, source if source.suffix.lower() == ".onnx" else None)
            shared = folder / SHARED_DATA
            if not self.has_shared_data():
                self.ensure_shared_data(progress, source=shared if shared.is_dir() else None, cancel=cancel)
            display = (name or "").strip() or model.stem.replace("_", " ").replace("-", " ")
            voice = self._install_folder(folder, display, CUSTOM, "Добавлен вами", model=model)
        logger.info("Свой голос добавлен: %s", voice.name)
        return voice

    @staticmethod
    def _find_model(folder, wanted=None):
        if wanted is not None:
            return wanted
        models = sorted(Path(folder).glob("*.onnx"), key=lambda p: p.stat().st_size, reverse=True)
        if not models:
            raise VoiceError("Не нашла файл голоса (.onnx). Нужна модель Piper или готовая папка sherpa-onnx.")
        return models[0]

    def _install_folder(self, folder, name, source, description, voice_id=None, model=None):
        folder = Path(folder)
        model = model or self._find_model(folder)
        data = model.read_bytes()
        if not looks_like_onnx(data):
            raise VoiceError("Этот файл не похож на модель ONNX.")
        config = self._read_piper_config(model)
        metadata = read_onnx_metadata(data)
        if "model_type" not in metadata:
            if config is None:
                raise VoiceError(
                    "Рядом с моделью нужен файл настроек .onnx.json (он лежит там же, где модель Piper).")
            data = add_onnx_metadata(data, self._piper_metadata(config))
            metadata = read_onnx_metadata(data)
        if metadata.get("model_type") != "vits":
            raise VoiceError("Поддерживаются только голоса Piper (VITS). Эта модель другого типа.")

        tokens = folder / "tokens.txt"
        if tokens.exists():
            tokens_text = tokens.read_text(encoding="utf-8")
        elif config is not None:
            tokens_text = self._tokens_from_config(config)
        else:
            raise VoiceError("Не нашла tokens.txt и файл .onnx.json, из которого его можно сделать.")

        voice_id = self._free_id(voice_id or slugify(name))
        target = self.root / voice_id
        target.mkdir(parents=True)
        try:
            (target / "model.onnx").write_bytes(data)
            (target / "tokens.txt").write_text(tokens_text, encoding="utf-8")
            details = {
                "name": name, "source": source, "description": description,
                "speakers": int(metadata.get("n_speakers", 1) or 1),
                "sample_rate": int(metadata.get("sample_rate", 22050) or 22050),
                "language": metadata.get("language", ""),
            }
            (target / "voice.json").write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as error:
            shutil.rmtree(target, ignore_errors=True)
            raise VoiceError(f"Не удалось сохранить голос: {error}")
        return self.get(voice_id)

    def _free_id(self, base):
        candidate, number = base, 2
        while (self.root / candidate).exists() or candidate == SHARED_DATA:
            candidate = f"{base}-{number}"
            number += 1
        return candidate

    @staticmethod
    def _read_piper_config(model):
        for candidate in (Path(str(model) + ".json"), Path(model).with_suffix(".json")):
            if candidate.exists():
                try:
                    data = json.loads(candidate.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(data, dict) and "phoneme_id_map" in data:
                    return data
        return None

    @staticmethod
    def _piper_metadata(config):
        language = config.get("language") or {}
        return {
            "model_type": "vits",
            "comment": "piper",
            "language": language.get("name_english") or language.get("code") or "",
            "voice": (config.get("espeak") or {}).get("voice", "ru"),
            "has_espeak": 1,
            "n_speakers": config.get("num_speakers", 1),
            "sample_rate": (config.get("audio") or {}).get("sample_rate", 22050),
        }

    @staticmethod
    def _tokens_from_config(config):
        lines = []
        for symbol, ids in config["phoneme_id_map"].items():
            if ids:
                lines.append(f"{symbol} {ids[0]}")
        return "\n".join(lines) + "\n"
