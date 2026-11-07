"""Whisper: speech recognition that is slower than Vosk but understands speech much better.

The model is an ONNX Whisper that sherpa-onnx runs. Every installed model lives in its own folder of data/stt/whisper:

    data/stt/whisper/<id>/encoder.onnx   the network that listens (int8, a quarter of the size of the original)
    data/stt/whisper/<id>/decoder.onnx   the network that writes the words
    data/stt/whisper/<id>/tokens.txt     its alphabet
    data/stt/whisper/<id>/model.json     the name and the size shown in the interface

Whisper hears a whole phrase at once, it cannot work with a stream. So the microphone is still watched by Vosk, which
finds where the phrase ends and shows its words while they are being said; Whisper then recognizes the recording of
the phrase (see SpeechRecognizer.listen_command).
"""
import json
import logging
import os
import shutil
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import requests

from src.productivity.planner import DATA_DIR
from src.speech.spoken import to_spoken

logger = logging.getLogger(__name__)

WHISPER_DIR = DATA_DIR / "stt" / "whisper"
MODELS_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
# Whisper takes at most 30 seconds at a time; a longer recording is cut into pieces that are a little shorter
MAX_CHUNK_SECONDS = 28.0
# A piece of the recording that is quieter than this (RMS, 1.0 is the full scale) has no speech, it is not recognized
SILENCE_RMS = 0.002
# Part of the archive that is a download, the rest is unpacking (for the progress bar)
DOWNLOAD_SHARE = 0.85

# The files that are taken out of an archive: the end of the name in the archive and the name they are given here
WANTED_FILES = (("-encoder.int8.onnx", "encoder.onnx"), ("-decoder.int8.onnx", "decoder.onnx"),
                ("-tokens.txt", "tokens.txt"))
MODEL_FILES = tuple(name for _, name in WANTED_FILES)
INFO_FILE = "model.json"


class SttError(Exception):
    """A problem that can be shown to the user as it is."""


class SttCancelled(SttError):
    """The user cancelled the download."""


@dataclass(frozen=True)
class WhisperModel:
    id: str
    title: str
    archive: str
    size_mb: int          # the download
    disk_mb: int          # what the three files take on the disk (and about as much in memory) once unpacked
    speed: str            # how fast it works on an ordinary computer
    quality: str          # how well it understands speech
    description: str

    @property
    def url(self):
        return MODELS_URL + self.archive


# The multilingual models of the sherpa-onnx release (the ones that understand Russian), from the smallest one
CATALOG = (
    WhisperModel("tiny", "Tiny", "sherpa-onnx-whisper-tiny.tar.bz2", 116, 99, "очень быстро",
                 "на русском часто ошибается", "Самая лёгкая. Подходит, чтобы просто попробовать."),
    WhisperModel("base", "Base", "sherpa-onnx-whisper-base.tar.bz2", 208, 160, "быстро",
                 "хорошо понимает короткие команды", "Разумный выбор для слабого компьютера."),
    WhisperModel("small", "Small", "sherpa-onnx-whisper-small.tar.bz2", 639, 358, "в несколько раз медленнее Base",
                 "точно понимает обычную речь", "Лучший баланс скорости и точности."),
    WhisperModel("turbo", "Turbo", "sherpa-onnx-whisper-turbo.tar.bz2", 564, 988, "медленно, нужен мощный процессор",
                 "очень точно", "Быстрая версия большой модели."),
    WhisperModel("large-v3", "Large v3", "sherpa-onnx-whisper-large-v3.tar.bz2", 1068, 1694,
                 "очень медленно, нужен мощный процессор", "самая точная", "Самая большая и точная модель."),
)


def catalog_entry(model_id):
    return next((entry for entry in CATALOG if entry.id == model_id), None)


# ---------- The text of Whisper ----------

# What Whisper writes when it has heard no speech at all: the credits of the subtitles it was taught on
NOISE_PHRASES = (
    "субтитры сделал", "субтитры создавал", "субтитры подогнал", "редактор субтитров", "корректор субтитров",
    "продолжение следует", "спасибо за просмотр", "подписывайтесь на канал", "dimatorzok", "amara.org",
    "thanks for watching", "subtitles by",
)


def is_noise_text(text):
    """True for what Whisper makes up on silence or noise: nothing, the credits of subtitles, one word on a loop."""
    spoken = to_spoken(text)
    if not spoken:
        return True
    if any(phrase in spoken for phrase in NOISE_PHRASES):
        return True
    words = spoken.split()
    return len(words) >= 6 and len(set(words)) <= 2


# ---------- The recording ----------

def pcm_to_samples(pcm: bytes):
    """16-bit mono PCM as floats from -1 to 1 (the form sherpa-onnx takes)."""
    return np.frombuffer(pcm[:len(pcm) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0


def is_silence(samples):
    return samples.size == 0 or float(np.sqrt(np.mean(np.square(samples)))) < SILENCE_RMS


def split_audio(samples, rate, limit_s=MAX_CHUNK_SECONDS, search_s=6.0):
    """The recording as pieces of at most limit_s seconds. A piece ends at the quietest moment of the last search_s
    seconds before the limit, so a word is not cut in half."""
    limit = int(limit_s * rate)
    if samples.size <= limit:
        return [samples]
    window = max(1, int(0.1 * rate))
    pieces, start = [], 0
    while samples.size - start > limit:
        end = start + limit
        low = max(start + limit // 2, end - int(search_s * rate))
        segment = samples[low:end]
        count = segment.size // window
        if count:
            energy = np.square(segment[:count * window]).reshape(count, window).mean(axis=1)
            end = low + int(np.argmin(energy)) * window + window // 2
        pieces.append(samples[start:end])
        start = end
    pieces.append(samples[start:])
    return pieces


# ---------- The model in memory ----------

def _sherpa_recognizer(encoder, decoder, tokens, language, threads):
    import sherpa_onnx
    return sherpa_onnx.OfflineRecognizer.from_whisper(
        encoder=str(encoder), decoder=str(decoder), tokens=str(tokens), language=language, task="transcribe",
        num_threads=threads, decoding_method="greedy_search",
    )


def default_threads():
    """Enough threads to be quick, not so many that the rest of the computer stops responding."""
    return max(1, min(4, os.cpu_count() or 1))


class WhisperEngine:
    """An installed Whisper model that is loaded into memory. load() takes seconds; transcribe() may be called from
    any thread (the calls follow one another)."""

    def __init__(self, folder, language="ru", threads=None, factory=None):
        self.folder = Path(folder)
        self.language = language or ""
        self.threads = threads or default_threads()
        self._factory = factory or _sherpa_recognizer
        self._recognizer = None
        self._lock = threading.Lock()

    @property
    def name(self):
        return self.folder.name

    @property
    def loaded(self):
        return self._recognizer is not None

    def load(self):
        files = [self.folder / name for name in MODEL_FILES]
        missing = [path.name for path in files if not path.is_file()]
        if missing:
            raise SttError(f"В папке модели не хватает файлов: {', '.join(missing)}.")
        encoder, decoder, tokens = files
        started = time.perf_counter()
        self._recognizer = self._factory(encoder, decoder, tokens, self.language, self.threads)
        logger.info("Whisper %s загружен за %.1f с (потоков: %s, язык: %s)", self.name,
                    time.perf_counter() - started, self.threads, self.language or "авто")

    def transcribe(self, pcm: bytes, rate: int):
        """The text of a recording (16-bit mono PCM) as Whisper wrote it; empty when there is no speech in it."""
        if self._recognizer is None:
            raise SttError("Модель Whisper не загружена.")
        parts = []
        for piece in split_audio(pcm_to_samples(pcm), rate):
            if is_silence(piece):
                continue
            text = self._decode(piece, rate)
            if text:
                parts.append(text)
        return " ".join(parts)

    def _decode(self, samples, rate):
        with self._lock:
            stream = self._recognizer.create_stream()
            stream.accept_waveform(rate, samples)
            self._recognizer.decode_stream(stream)
            return (stream.result.text or "").strip()


# ---------- The models on the disk ----------

class WhisperLibrary:
    """The installed models, and the download of the models of the catalog."""

    def __init__(self, root=None, downloader=None):
        self.root = Path(root) if root else WHISPER_DIR
        # downloader(url, destination, progress, cancel) - replaceable in tests
        self._download = downloader or self._http_download

    def folder(self, model_id):
        return self.root / model_id

    def is_installed(self, model_id):
        folder = self.folder(model_id)
        return folder.is_dir() and all((folder / name).is_file() for name in MODEL_FILES)

    def installed(self):
        """The ids of the installed models: the ones of the catalog first, then the rest by name."""
        if not self.root.is_dir():
            return []
        found = [folder.name for folder in sorted(self.root.iterdir()) if self.is_installed(folder.name)]
        order = [entry.id for entry in CATALOG]
        return sorted(found, key=lambda name: (order.index(name) if name in order else len(order), name))

    def title(self, model_id):
        entry = catalog_entry(model_id)
        return f"Whisper {entry.title}" if entry else f"Whisper {model_id}"

    def disk_size(self, model_id):
        """Bytes the installed model takes."""
        folder = self.folder(model_id)
        return sum(path.stat().st_size for path in folder.iterdir() if path.is_file()) if folder.is_dir() else 0

    def remove(self, model_id):
        if not self.is_installed(model_id):
            raise SttError("Такой модели нет.")
        shutil.rmtree(self.folder(model_id), ignore_errors=True)
        logger.info("Модель Whisper удалена: %s", model_id)

    # ---------- Download ----------

    @staticmethod
    def _http_download(url, destination, progress=None, cancel=None):
        """Downloads a file. A part that was downloaded before is continued (the server may refuse: then it starts
        over). The part stays on the disk if the download is cancelled or breaks, so it is not lost."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            have = destination.stat().st_size if destination.exists() else 0
            headers = {"Range": f"bytes={have}-"} if have else {}
            try:
                with requests.get(url, stream=True, timeout=30, headers=headers) as response:
                    if response.status_code == 416 and have:
                        # The part is at least as big as the file: it is not a part of this download
                        destination.unlink()
                        continue
                    response.raise_for_status()
                    resumed = response.status_code == 206 and have > 0
                    if not resumed:
                        have = 0
                    total = have + int(response.headers.get("Content-Length") or 0)
                    done = have
                    with open(destination, "ab" if resumed else "wb") as out:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if cancel is not None and cancel.is_set():
                                raise SttCancelled("Загрузка отменена.")
                            out.write(chunk)
                            done += len(chunk)
                            if progress and total:
                                progress(min(done / total, 1.0))
                return
            except requests.RequestException as error:
                raise SttError(f"Не удалось скачать модель. Проверьте интернет и попробуйте ещё раз. ({error})")
            except OSError as error:
                raise SttError(f"Не удалось записать файл на диск: {error}")
        raise SttError("Сервер не отдаёт файл. Попробуйте позже.")

    @staticmethod
    def _unpack(archive, target, progress=None, cancel=None):
        """Takes the three files of the model out of the archive (the rest of it, the unneeded copies of the
        network, is skipped); they are put into target under the names of MODEL_FILES."""
        found = set()
        try:
            with tarfile.open(archive, "r:bz2") as tar:
                for member in tar:
                    if not member.isfile():
                        continue
                    name = next((own for suffix, own in WANTED_FILES if member.name.endswith(suffix)), None)
                    if name is None or name in found:
                        continue
                    if cancel is not None and cancel.is_set():
                        raise SttCancelled("Загрузка отменена.")
                    with tar.extractfile(member) as source, open(Path(target) / name, "wb") as out:
                        shutil.copyfileobj(source, out, 1024 * 1024)
                    found.add(name)
                    if progress:
                        progress(len(found) / len(WANTED_FILES))
                    if len(found) == len(WANTED_FILES):
                        break
        except (tarfile.TarError, EOFError, OSError) as error:
            raise SttError(f"Не удалось распаковать архив: {error}")
        missing = [name for name in MODEL_FILES if name not in found]
        if missing:
            raise SttError(f"В архиве нет файлов модели: {', '.join(missing)}.")

    def download(self, model_id, progress=None, cancel=None):
        """Downloads a model of the catalog and installs it; progress(fraction) is called while it works."""
        entry = catalog_entry(model_id)
        if entry is None:
            raise SttError("Такой модели нет в каталоге.")
        if self.is_installed(model_id):
            return self.folder(model_id)
        self.root.mkdir(parents=True, exist_ok=True)
        archive = self.root / ".downloads" / entry.archive

        def downloading(fraction):
            if progress:
                progress(fraction * DOWNLOAD_SHARE)

        def unpacking(fraction):
            if progress:
                progress(DOWNLOAD_SHARE + fraction * (1 - DOWNLOAD_SHARE))

        self._download(entry.url, archive, downloading, cancel)
        # The model is put together aside and moved to its place at once: a model that is half there is never seen
        work = Path(tempfile.mkdtemp(dir=self.root, prefix=f".{model_id}-"))
        try:
            self._unpack(archive, work, unpacking, cancel)
            info = {"id": entry.id, "title": entry.title, "size_mb": entry.size_mb, "disk_mb": entry.disk_mb}
            (work / INFO_FILE).write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
            shutil.rmtree(self.folder(model_id), ignore_errors=True)
            work.rename(self.folder(model_id))
        except BaseException as error:
            shutil.rmtree(work, ignore_errors=True)
            if not isinstance(error, SttCancelled):
                # An archive that cannot be unpacked is broken: the next try has to download it again
                archive.unlink(missing_ok=True)
            raise
        archive.unlink(missing_ok=True)
        logger.info("Модель Whisper установлена: %s", entry.id)
        return self.folder(model_id)
