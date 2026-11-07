"""Whisper: the text it writes, the recording it is given, the model in memory, the library of models and the settings.

The network of Whisper is replaced by a script and the download by a local server, so the tests need neither a model
nor the internet (the real model is tried in test_whisper_e2e.py when there is one).
"""
import contextlib
import io
import shutil
import sys
import tarfile
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.speech import whisper_engine as we  # noqa: E402
from src.speech.stt_settings import SttSettings  # noqa: E402

RATE = 16000


def pcm_of(samples):
    return (np.asarray(samples) * 32767).astype("<i2").tobytes()


def tone(seconds, level=0.2):
    return np.sin(np.arange(int(seconds * RATE)) * 0.05).astype(np.float32) * level


class TextTests(unittest.TestCase):
    def test_the_text_is_brought_to_the_form_of_a_spoken_command(self):
        self.assertEqual(we.to_spoken("Поставь таймер на 5 минут, пожалуйста!"), "поставь таймер на 5 минут пожалуйста")
        self.assertEqual(we.to_spoken("Ещё раз: в 7:30"), "еще раз в 7:30")
        # a decimal number stays one number, the comma of a sentence goes
        self.assertEqual(we.to_spoken("Поставь таймер на 1,5 часа."), "поставь таймер на 1.5 часа")
        self.assertEqual(we.to_spoken("Через 2,5 минуты, пожалуйста"), "через 2.5 минуты пожалуйста")
        self.assertEqual(we.to_spoken("  \n "), "")
        self.assertEqual(we.to_spoken(None), "")

    def test_what_whisper_makes_up_on_silence_is_noise(self):
        for text in ("", "   ", "Субтитры сделал DimaTorzok", "Продолжение следует...", "Спасибо за просмотр!",
                     "Редактор субтитров А.Семкин", "а а а а а а а", "Thanks for watching"):
            self.assertTrue(we.is_noise_text(text), text)

    def test_real_phrases_are_not_noise(self):
        for text in ("Выключи компьютер.", "Спасибо", "включи субтитры", "да да да", "Поставь таймер на 5 минут"):
            self.assertFalse(we.is_noise_text(text), text)


class RecordingTests(unittest.TestCase):
    def test_pcm_becomes_floats_from_minus_one_to_one(self):
        samples = we.pcm_to_samples(np.array([0, 16384, -32768], dtype="<i2").tobytes())
        self.assertEqual(samples.dtype, np.float32)
        np.testing.assert_allclose(samples, [0.0, 0.5, -1.0])

    def test_a_stray_byte_at_the_end_is_dropped(self):
        self.assertEqual(we.pcm_to_samples(b"\x00\x01\x02").size, 1)

    def test_silence_is_told_from_speech(self):
        self.assertTrue(we.is_silence(np.zeros(RATE, dtype=np.float32)))
        self.assertTrue(we.is_silence(np.zeros(0, dtype=np.float32)))
        self.assertTrue(we.is_silence(np.full(RATE, 0.0005, dtype=np.float32)))
        self.assertFalse(we.is_silence(tone(1.0)))

    def test_a_short_recording_is_not_cut(self):
        samples = tone(10)
        pieces = we.split_audio(samples, RATE)
        self.assertEqual(len(pieces), 1)
        self.assertIs(pieces[0], samples)

    def test_a_long_recording_is_cut_into_pieces_that_make_it_whole_again(self):
        samples = tone(75)
        pieces = we.split_audio(samples, RATE)
        self.assertGreaterEqual(len(pieces), 3)
        self.assertTrue(all(piece.size <= we.MAX_CHUNK_SECONDS * RATE for piece in pieces))
        np.testing.assert_array_equal(np.concatenate(pieces), samples)

    def test_the_cut_falls_into_the_quietest_moment_so_a_word_is_not_split(self):
        samples = tone(40)
        pause_from = int(24.0 * RATE)
        samples[pause_from:pause_from + int(0.4 * RATE)] = 0.0       # a pause 4 seconds before the limit
        pieces = we.split_audio(samples, RATE)
        cut = pieces[0].size
        self.assertGreaterEqual(cut, pause_from)
        self.assertLessEqual(cut, pause_from + int(0.4 * RATE))


class FakeSherpa:
    """What sherpa-onnx gives: a recognizer that writes the text of the script and remembers what it was given."""

    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = []
        self.active = 0
        self.overlap = False

    def create_stream(self):
        return types_stream()

    def decode_stream(self, stream):
        self.active += 1
        self.overlap = self.overlap or self.active > 1
        self.calls.append((stream.rate, stream.samples.size))
        stream.result.text = self.texts.pop(0) if self.texts else ""
        self.active -= 1


def types_stream():
    import types
    stream = types.SimpleNamespace(rate=None, samples=None, result=types.SimpleNamespace(text=""))

    def accept(rate, samples):
        stream.rate, stream.samples = rate, samples

    stream.accept_waveform = accept
    return stream


def make_model_folder(root, name="base"):
    folder = Path(root) / name
    folder.mkdir(parents=True)
    for file in we.MODEL_FILES:
        (folder / file).write_bytes(b"data")
    return folder


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.folder = make_model_folder(self.dir.name)
        self.created = []

    def engine(self, texts, **options):
        sherpa = FakeSherpa(texts)

        def factory(encoder, decoder, tokens, language, threads):
            self.created.append((Path(encoder).name, Path(decoder).name, Path(tokens).name, language, threads))
            return sherpa

        engine = we.WhisperEngine(self.folder, factory=factory, **options)
        engine.load()
        return engine, sherpa

    def test_the_model_is_made_from_its_three_files_with_the_language_and_the_threads(self):
        engine, _ = self.engine([], language="ru", threads=3)
        self.assertEqual(self.created, [("encoder.onnx", "decoder.onnx", "tokens.txt", "ru", 3)])
        self.assertTrue(engine.loaded)
        self.assertEqual(engine.name, "base")

    def test_the_language_may_be_left_to_whisper(self):
        self.engine([], language=None)
        self.assertEqual(self.created[0][3], "")

    def test_a_folder_with_missing_files_is_refused(self):
        (self.folder / "decoder.onnx").unlink()
        with self.assertRaisesRegex(we.SttError, "decoder.onnx"):
            we.WhisperEngine(self.folder, factory=lambda *a: None).load()

    def test_a_model_that_is_not_loaded_cannot_recognize(self):
        with self.assertRaises(we.SttError):
            we.WhisperEngine(self.folder).transcribe(pcm_of(tone(1)), RATE)

    def test_the_recording_goes_to_whisper_with_its_rate(self):
        engine, sherpa = self.engine(["Выключи компьютер."])
        self.assertEqual(engine.transcribe(pcm_of(tone(2)), 48000), "Выключи компьютер.")
        self.assertEqual(sherpa.calls, [(48000, 2 * RATE)])

    def test_silence_is_not_given_to_whisper_at_all(self):
        engine, sherpa = self.engine(["Субтитры сделал DimaTorzok"])
        self.assertEqual(engine.transcribe(pcm_of(np.zeros(RATE)), RATE), "")
        self.assertEqual(sherpa.calls, [])

    def test_a_long_recording_is_recognized_piece_by_piece_and_the_texts_are_joined(self):
        engine, sherpa = self.engine(["первая часть", "вторая часть", "третья часть"])
        text = engine.transcribe(pcm_of(tone(70)), RATE)
        self.assertEqual(text, "первая часть вторая часть третья часть")
        self.assertEqual(len(sherpa.calls), 3)
        self.assertTrue(all(size <= we.MAX_CHUNK_SECONDS * RATE for _, size in sherpa.calls))

    def test_calls_from_two_threads_never_overlap(self):
        engine, sherpa = self.engine(["один", "два", "три", "четыре"])
        threads = [threading.Thread(target=engine.transcribe, args=(pcm_of(tone(1)), RATE)) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(sherpa.calls), 4)
        self.assertFalse(sherpa.overlap)


# ---------- The library ----------

def make_archive(path, name="base", with_tokens=True, with_int8=True):
    """An archive like the ones of sherpa-onnx: the original network next to the int8 one, the tokens and test files."""
    path = Path(path)
    files = {
        f"sherpa-onnx-whisper-{name}/{name}-encoder.onnx": b"fp32 encoder " * 50,
        f"sherpa-onnx-whisper-{name}/{name}-decoder.onnx": b"fp32 decoder " * 50,
        f"sherpa-onnx-whisper-{name}/test_wavs/0.wav": b"RIFF",
    }
    if with_int8:
        files[f"sherpa-onnx-whisper-{name}/{name}-encoder.int8.onnx"] = b"int8 encoder"
        files[f"sherpa-onnx-whisper-{name}/{name}-decoder.int8.onnx"] = b"int8 decoder"
    if with_tokens:
        files[f"sherpa-onnx-whisper-{name}/{name}-tokens.txt"] = b"tokens"
    with tarfile.open(path, "w:bz2") as tar:
        for member, data in files.items():
            info = tarfile.TarInfo(member)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name) / "whisper"
        self.source = make_archive(Path(self.dir.name) / "source.tar.bz2")
        self.requested = []
        self.progress = []

    def library(self, downloader=None, source=None):
        source = source or self.source

        def copy(url, destination, progress=None, cancel=None):
            self.requested.append(url)
            Path(destination).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(source, destination)
            if progress:
                progress(0.5)
                progress(1.0)

        return we.WhisperLibrary(self.root, downloader or copy)

    def test_nothing_is_installed_at_first(self):
        library = self.library()
        self.assertEqual(library.installed(), [])
        self.assertFalse(library.is_installed("base"))

    def test_the_catalog_has_the_models_that_understand_russian(self):
        ids = [entry.id for entry in we.CATALOG]
        self.assertEqual(ids[:2], ["tiny", "base"])
        self.assertTrue(all(entry.url.startswith("https://github.com/k2-fsa/sherpa-onnx/releases/") for entry in we.CATALOG))
        self.assertFalse(any(entry.id.endswith(".en") for entry in we.CATALOG))

    def test_a_model_is_downloaded_and_only_the_three_files_it_needs_are_kept(self):
        library = self.library()
        folder = library.download("base", progress=self.progress.append)
        self.assertEqual(self.requested, [we.catalog_entry("base").url])
        self.assertEqual(sorted(path.name for path in folder.iterdir()),
                         sorted([*we.MODEL_FILES, we.INFO_FILE]))
        self.assertEqual((folder / "encoder.onnx").read_bytes(), b"int8 encoder")
        self.assertEqual((folder / "decoder.onnx").read_bytes(), b"int8 decoder")
        self.assertEqual((folder / "tokens.txt").read_bytes(), b"tokens")
        self.assertTrue(library.is_installed("base"))
        self.assertEqual(library.installed(), ["base"])
        self.assertGreater(library.disk_size("base"), 0)

    def test_the_progress_runs_forward_and_ends_at_one(self):
        self.library().download("base", progress=self.progress.append)
        self.assertEqual(self.progress, sorted(self.progress))
        self.assertAlmostEqual(self.progress[-1], 1.0)
        self.assertTrue(all(0 <= value <= 1 for value in self.progress))

    def test_no_traces_are_left_after_the_install(self):
        library = self.library()
        library.download("base")
        self.assertEqual([p.name for p in self.root.iterdir()], ["base", ".downloads"])
        self.assertEqual(list((self.root / ".downloads").iterdir()), [])

    def test_an_installed_model_is_not_downloaded_again(self):
        library = self.library()
        library.download("base")
        library.download("base")
        self.assertEqual(len(self.requested), 1)

    def test_a_model_that_is_not_in_the_catalog_is_refused(self):
        with self.assertRaises(we.SttError):
            self.library().download("whisper-9000")

    def test_installed_models_are_listed_in_the_order_of_the_catalog_with_the_own_ones_last(self):
        library = self.library()
        library.download("small")
        library.download("tiny")
        make_model_folder(self.root, "my-model")
        (self.root / "incomplete").mkdir()
        self.assertEqual(library.installed(), ["tiny", "small", "my-model"])

    def test_a_model_is_removed(self):
        library = self.library()
        library.download("base")
        library.remove("base")
        self.assertFalse(library.is_installed("base"))
        with self.assertRaises(we.SttError):
            library.remove("base")

    def test_a_cancelled_download_leaves_no_model(self):
        def cancelling(url, destination, progress=None, cancel=None):
            raise we.SttCancelled("Загрузка отменена.")
        library = self.library(cancelling)
        with self.assertRaises(we.SttCancelled):
            library.download("base")
        self.assertEqual(library.installed(), [])

    def test_a_broken_archive_is_reported_and_thrown_away(self):
        broken = Path(self.dir.name) / "broken.tar.bz2"
        broken.write_bytes(b"this is not an archive")
        library = self.library(source=broken)
        with self.assertRaisesRegex(we.SttError, "распаковать"):
            library.download("base")
        self.assertEqual(library.installed(), [])
        self.assertEqual(list((self.root / ".downloads").iterdir()), [], "the next try has to download it again")
        self.assertEqual([p.name for p in self.root.iterdir()], [".downloads"], "no half-made model folder")

    def test_an_archive_without_the_int8_network_is_reported(self):
        library = self.library(source=make_archive(Path(self.dir.name) / "odd.tar.bz2", with_int8=False))
        with self.assertRaisesRegex(we.SttError, "encoder.onnx"):
            library.download("base")
        self.assertEqual(library.installed(), [])

    def test_an_archive_without_tokens_is_reported(self):
        library = self.library(source=make_archive(Path(self.dir.name) / "odd.tar.bz2", with_tokens=False))
        with self.assertRaisesRegex(we.SttError, "tokens.txt"):
            library.download("base")

    def test_a_model_that_is_replaced_by_a_new_download_is_whole(self):
        library = self.library()
        folder = library.download("base")
        (folder / "tokens.txt").unlink()
        self.assertFalse(library.is_installed("base"))
        library.download("base")
        self.assertTrue(library.is_installed("base"))


# ---------- The download over HTTP ----------

BLOB = bytes(range(256)) * 16384          # 4 MiB


class Server(BaseHTTPRequestHandler):
    mode = "ranges"                       # ranges, norange, missing
    seen = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        Server.seen.append(self.headers.get("Range"))
        if Server.mode == "missing":
            self.send_error(404)
            return
        start = 0
        header = self.headers.get("Range")
        if header and Server.mode == "ranges":
            start = int(header.split("=")[1].split("-")[0])
            if start >= len(BLOB):
                self.send_response(416)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(206)
        else:
            self.send_response(200)
        body = BLOB[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except ConnectionError:
            pass                           # the client has cancelled the download and gone away


class HttpDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Server)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/model.tar.bz2"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Server.mode, Server.seen = "ranges", []
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.target = Path(self.dir.name) / "part" / "model.tar.bz2"
        self.progress = []

    def download(self, **options):
        we.WhisperLibrary._http_download(self.url, self.target, self.progress.append, **options)

    def test_a_file_is_downloaded_with_a_progress(self):
        self.download()
        self.assertEqual(self.target.read_bytes(), BLOB)
        self.assertEqual(self.progress, sorted(self.progress))
        self.assertAlmostEqual(self.progress[-1], 1.0)

    def test_a_part_that_is_there_is_continued(self):
        self.target.parent.mkdir()
        self.target.write_bytes(BLOB[:1_000_000])
        self.download()
        self.assertEqual(self.target.read_bytes(), BLOB)
        self.assertEqual(Server.seen, ["bytes=1000000-"])
        self.assertGreater(self.progress[0], 0.2, "the progress starts where the part ends")

    def test_a_server_that_ignores_the_range_gives_the_whole_file_and_it_is_not_doubled(self):
        Server.mode = "norange"
        self.target.parent.mkdir()
        self.target.write_bytes(BLOB[:1_000_000])
        self.download()
        self.assertEqual(self.target.read_bytes(), BLOB)

    def test_a_leftover_that_is_bigger_than_the_file_starts_the_download_over(self):
        self.target.parent.mkdir()
        self.target.write_bytes(BLOB + b"junk")
        self.download()
        self.assertEqual(self.target.read_bytes(), BLOB)

    def test_a_cancelled_download_keeps_what_it_has_for_the_next_time(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(we.SttCancelled):
            self.download(cancel=cancel)
        self.assertLess(self.target.stat().st_size, len(BLOB))
        cancel.clear()
        self.download(cancel=cancel)
        self.assertEqual(self.target.read_bytes(), BLOB)

    def test_a_missing_file_is_reported(self):
        Server.mode = "missing"
        with self.assertRaisesRegex(we.SttError, "Не удалось скачать"):
            self.download()

    def test_a_server_that_cannot_be_reached_is_reported(self):
        with self.assertRaisesRegex(we.SttError, "Не удалось скачать"):
            we.WhisperLibrary._http_download("http://127.0.0.1:9/none", self.target)


# ---------- The settings ----------

class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "stt.json"

    def test_vosk_and_russian_are_the_defaults(self):
        settings = SttSettings(self.path)
        self.assertEqual((settings.engine, settings.whisper_model, settings.language), ("vosk", None, "ru"))

    def test_the_choice_is_saved_and_read_back(self):
        settings = SttSettings(self.path)
        self.assertTrue(settings.update(engine="whisper", whisper_model="base", language="en"))
        again = SttSettings(self.path)
        self.assertEqual((again.engine, again.whisper_model, again.language), ("whisper", "base", "en"))

    def test_the_same_choice_changes_nothing(self):
        settings = SttSettings(self.path)
        settings.update(engine="whisper")
        self.assertFalse(settings.update(engine="whisper"))
        self.assertFalse(settings.update())

    def test_unknown_values_are_not_taken(self):
        settings = SttSettings(self.path)
        settings.update(engine="google", language="xx")
        self.assertEqual((settings.engine, settings.language), ("vosk", "ru"))

    def test_the_model_can_be_cleared(self):
        settings = SttSettings(self.path)
        settings.update(whisper_model="base")
        settings.update(whisper_model="")
        self.assertIsNone(settings.whisper_model)

    def test_the_language_can_be_left_to_whisper(self):
        settings = SttSettings(self.path)
        settings.update(language="")
        self.assertEqual(SttSettings(self.path).language, "")

    def test_a_damaged_file_gives_the_defaults(self):
        for content in ("{not json", "[]", '{"engine": 5, "whisper_model": 7, "language": null}'):
            self.path.write_text(content, encoding="utf-8")
            with self.assertLogs("src.speech.stt_settings", level="ERROR") if content == "{not json" \
                    else contextlib.nullcontext():
                settings = SttSettings(self.path)
            self.assertEqual((settings.engine, settings.whisper_model, settings.language), ("vosk", None, "ru"))


if __name__ == "__main__":
    unittest.main()
