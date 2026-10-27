import importlib.util
import io
import shutil
import sys
import tempfile
import threading
import types
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LOADED = []          # paths that vosk.Model was asked to load
FAIL_ON = set()      # names of model folders that fail to load
RECOGNIZERS = []     # models that KaldiRecognizer was created with


class FakeModel:
    def __init__(self, path):
        if Path(path).name in FAIL_ON:
            raise RuntimeError("not enough memory")
        self.path = path
        LOADED.append(path)


class StopHere(Exception):
    pass


def fake_recognizer(model, samplerate, *args):
    RECOGNIZERS.append(model)
    raise StopHere()  # enough: listen_command reports every error as "nothing recognized"


fake_sd = types.ModuleType("sounddevice")
fake_sd.default = types.SimpleNamespace(device=(0, 0))
fake_sd.query_devices = lambda *a, **k: {"default_samplerate": 16000}
fake_vosk = types.ModuleType("vosk")
fake_vosk.Model = FakeModel
fake_vosk.KaldiRecognizer = fake_recognizer
sys.modules["sounddevice"] = fake_sd
sys.modules["vosk"] = fake_vosk

from src.speech import speech_module as sm  # noqa: E402

spec = importlib.util.spec_from_file_location("download_vosk_model", ROOT / "scripts" / "download_vosk_model.py")
downloader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(downloader)


def make_model_dir(assets: Path, name: str, complete=True):
    for part in ("am", "conf", "graph") if complete else ("am",):
        (assets / name / part).mkdir(parents=True, exist_ok=True)
    return assets / name


class FindBigModelTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.assets = Path(self.dir.name)

    def test_nothing_downloaded(self):
        self.assertIsNone(sm.find_big_model(self.assets))

    def test_the_small_model_is_not_a_big_one(self):
        make_model_dir(self.assets, "vosk-model-small-ru-0.22")
        self.assertIsNone(sm.find_big_model(self.assets))

    def test_unfinished_unpacking_is_ignored(self):
        make_model_dir(self.assets, "vosk-model-ru-0.42", complete=False)
        self.assertIsNone(sm.find_big_model(self.assets))

    def test_finds_the_model(self):
        path = make_model_dir(self.assets, "vosk-model-ru-0.22")
        self.assertEqual(sm.find_big_model(self.assets), path)

    def test_the_newer_model_wins(self):
        make_model_dir(self.assets, "vosk-model-ru-0.22")
        newer = make_model_dir(self.assets, "vosk-model-ru-0.42")
        self.assertEqual(sm.find_big_model(self.assets), newer)


class BigModelLoadingTest(unittest.TestCase):
    def setUp(self):
        LOADED.clear()
        FAIL_ON.clear()
        RECOGNIZERS.clear()
        self.messages = []

    def recognizer(self, big):
        recognizer = sm.SpeechRecognizer(input_device=0, big_model_path=big)
        recognizer.on_model = self.messages.append
        return recognizer

    def test_without_a_big_model_commands_use_the_small_one(self):
        recognizer = self.recognizer(None)
        self.assertIsNone(recognizer.start_big_model_loading())
        self.assertIs(recognizer.command_model, recognizer.model)
        self.assertEqual(self.messages, [])

    def test_small_model_works_until_the_big_one_is_loaded(self):
        recognizer = self.recognizer(Path("assets/vosk-model-ru-0.42"))
        # Nothing is loaded until the loading is started, the callbacks have to be set first
        self.assertIs(recognizer.command_model, recognizer.model)
        self.assertEqual(len(LOADED), 1)

    def test_big_model_takes_over_the_commands(self):
        recognizer = self.recognizer(Path("assets/vosk-model-ru-0.42"))
        recognizer.start_big_model_loading().join(5)
        self.assertIsNot(recognizer.command_model, recognizer.model)
        self.assertEqual(Path(recognizer.command_model.path).name, "vosk-model-ru-0.42")
        self.assertEqual(len(self.messages), 2)
        self.assertIn("Загружаю", self.messages[0])
        self.assertIn("готова", self.messages[1])

    def test_loading_starts_only_once(self):
        recognizer = self.recognizer(Path("assets/vosk-model-ru-0.42"))
        recognizer.start_big_model_loading().join(5)
        self.assertIsNone(recognizer.start_big_model_loading())
        self.assertEqual(len(LOADED), 2)  # the small model and the big one, nothing more

    def test_failed_loading_keeps_the_small_model(self):
        FAIL_ON.add("vosk-model-ru-0.42")
        recognizer = self.recognizer(Path("assets/vosk-model-ru-0.42"))
        recognizer.start_big_model_loading().join(5)
        self.assertIs(recognizer.command_model, recognizer.model)
        self.assertIn("не удалось", self.messages[-1])

    def test_a_broken_callback_does_not_stop_the_loading(self):
        recognizer = sm.SpeechRecognizer(input_device=0, big_model_path=Path("assets/vosk-model-ru-0.42"))
        recognizer.on_model = mock.Mock(side_effect=RuntimeError("interface is gone"))
        recognizer.start_big_model_loading().join(5)
        self.assertIsNot(recognizer.command_model, recognizer.model)

    def test_commands_are_recognized_by_the_command_model(self):
        recognizer = self.recognizer(Path("assets/vosk-model-ru-0.42"))
        recognizer.listen_command()
        self.assertIs(RECOGNIZERS[-1], recognizer.model)
        recognizer.start_big_model_loading().join(5)
        recognizer.listen_command()
        self.assertIs(RECOGNIZERS[-1], recognizer.command_model)
        self.assertIsNot(RECOGNIZERS[-1], recognizer.model)


# A small web server with the "model": it understands Range requests like the real one
class ModelServer:
    def __init__(self, files):
        files_ = files
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                data = files_.get(self.path.lstrip("/"))
                if data is None:
                    self.send_error(404)
                    return
                start = 0
                header = self.headers.get("Range")
                owner.requests.append(header)
                if header:
                    start = int(header.split("=")[1].rstrip("-"))
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                else:
                    self.send_response(200)
                self.send_header("Content-Length", str(len(data) - start))
                self.end_headers()
                self.wfile.write(data[start:])

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def model_zip(name, parts=("am", "conf", "graph"), top=True) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for part in parts:
            zf.writestr(f"{name + '/' if top else ''}{part}/file.bin", b"x" * 20000)
    return buffer.getvalue()


class DownloadScriptTest(unittest.TestCase):
    NAME = "vosk-model-ru-0.42"

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.assets = Path(self.dir.name)
        self.data = model_zip(self.NAME)
        self.server = ModelServer({self.NAME + ".zip": self.data})
        self.addCleanup(self.server.close)
        self.quiet = lambda text, end="\n": None

    def run_main(self, *extra):
        argv = [self.NAME, "--base-url", self.server.url, "--assets", str(self.assets), *extra]
        with mock.patch("builtins.print"):
            return downloader.main(argv)

    def test_downloads_and_unpacks_a_model_the_assistant_can_find(self):
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(sm.find_big_model(self.assets), self.assets / self.NAME)
        self.assertEqual(list(self.assets.glob("*.part")), [])
        self.assertEqual(list(self.assets.glob("*.unpacking")), [])

    def test_does_nothing_when_the_model_is_there(self):
        self.assertEqual(self.run_main(), 0)
        before = len(self.server.requests)
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(len(self.server.requests), before)

    def test_continues_an_interrupted_download(self):
        part = self.assets / f"{self.NAME}.zip.part"
        part.write_bytes(self.data[:5000])
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.server.requests, ["bytes=5000-"])
        self.assertEqual(sm.find_big_model(self.assets), self.assets / self.NAME)

    def test_server_without_range_support_restarts_the_download(self):
        part = self.assets / f"{self.NAME}.zip.part"
        part.write_bytes(self.data[:5000])
        server = ModelServer({self.NAME + ".zip": self.data})
        self.addCleanup(server.close)
        # This server ignores the header and sends the whole file with status 200
        original = downloader.urllib.request.Request

        def without_range(url, headers=None):
            return original(url)

        with mock.patch.object(downloader.urllib.request, "Request", without_range):
            size = downloader.download(server.url + self.NAME + ".zip", part, self.quiet)
        self.assertEqual(size, len(self.data))

    def test_not_enough_disk_space(self):
        usage = types.SimpleNamespace(total=100, used=100, free=1000)
        with mock.patch.object(downloader.shutil, "disk_usage", return_value=usage):
            with self.assertRaises(RuntimeError) as caught:
                downloader.download(self.server.url + self.NAME + ".zip", self.assets / "m.part", self.quiet)
        self.assertIn("Мало места", str(caught.exception))

    def test_unknown_model_is_an_error_not_a_crash(self):
        with mock.patch("builtins.print"):
            self.assertEqual(downloader.main(["no-such-model", "--base-url", self.server.url,
                                              "--assets", str(self.assets)]), 1)

    def test_archive_without_the_model_folder(self):
        archive = self.assets / "x.zip"
        archive.write_bytes(model_zip(self.NAME, top=False))
        with self.assertRaises(RuntimeError):
            downloader.unpack(archive, self.assets, self.NAME, self.quiet)
        self.assertFalse((self.assets / self.NAME).exists())
        self.assertEqual(list(self.assets.glob("*.unpacking")), [])

    def test_broken_archive_leaves_no_model(self):
        archive = self.assets / "x.zip"
        archive.write_bytes(b"this is not a zip file")
        with self.assertRaises(zipfile.BadZipFile):
            downloader.unpack(archive, self.assets, self.NAME, self.quiet)
        self.assertFalse((self.assets / self.NAME).exists())

    def test_old_copy_of_the_model_is_replaced(self):
        old = make_model_dir(self.assets, self.NAME)
        (old / "stale.txt").write_text("old")
        archive = self.assets / "x.zip"
        archive.write_bytes(self.data)
        final = downloader.unpack(archive, self.assets, self.NAME, self.quiet)
        self.assertFalse((final / "stale.txt").exists())
        self.assertTrue((final / "am").is_dir())


if __name__ == "__main__":
    unittest.main()
