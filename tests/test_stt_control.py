"""The choice of the engine (Vosk or Whisper), the model and the language: what is saved and what the recognizer is told."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.speech.stt_control import SttControl  # noqa: E402
from src.speech.stt_settings import SttSettings  # noqa: E402
from src.speech.whisper_engine import MODEL_FILES, SttError, WhisperLibrary  # noqa: E402


class FakeRecognizer:
    """What SttControl asks of the recognizer: the engine to use (the folder of the model and the language)."""

    def __init__(self):
        self.calls = []
        self.engine = "vosk"
        self.whisper_state = "none"

    def use_engine(self, engine, folder=None, language="ru", threads=None, factory=None):
        self.calls.append((engine, folder, language))
        self.engine = engine
        self.whisper_state = "loading" if engine == "whisper" and folder else "none"


class SttControlTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        self.recognizer = FakeRecognizer()
        self.control = self.make()

    def make(self):
        return SttControl(self.recognizer, SttSettings(self.root / "stt.json"), WhisperLibrary(self.root / "whisper"))

    def install(self, model_id):
        folder = self.root / "whisper" / model_id
        folder.mkdir(parents=True, exist_ok=True)
        for name in MODEL_FILES:
            (folder / name).write_bytes(b"x")
        return folder

    def test_nothing_is_started_for_vosk(self):
        self.control.start()
        self.assertEqual(self.recognizer.calls, [])

    def test_the_saved_choice_of_whisper_is_started_with_its_model_and_language(self):
        folder = self.install("base")
        self.control.settings.update(engine="whisper", whisper_model="base", language="en")
        self.control.start()
        self.assertEqual(self.recognizer.calls, [("whisper", folder, "en")])

    def test_a_saved_model_that_is_gone_is_started_as_no_model(self):
        self.control.settings.update(engine="whisper", whisper_model="base")
        self.control.start()
        self.assertEqual(self.recognizer.calls, [("whisper", None, "ru")])

    def test_whisper_without_a_model_is_saved_and_the_recognizer_is_told_it_has_none(self):
        self.control.set_engine("whisper")
        self.assertEqual(self.recognizer.calls, [("whisper", None, "ru")])
        self.assertEqual(self.make().settings.engine, "whisper")

    def test_a_model_that_is_not_downloaded_cannot_be_chosen(self):
        with self.assertRaises(SttError):
            self.control.set_whisper("base")
        self.assertIsNone(self.control.settings.whisper_model)
        self.assertEqual(self.recognizer.calls, [])

    def test_the_model_is_chosen_for_later_while_vosk_works(self):
        self.install("base")
        self.control.set_whisper("base")
        self.assertEqual(self.control.settings.whisper_model, "base")
        self.assertEqual(self.recognizer.calls, [], "Vosk is the engine: nothing is loaded")

    def test_choosing_a_model_while_whisper_is_the_engine_loads_it(self):
        base, small = self.install("base"), self.install("small")
        self.control.set_whisper("base")
        self.control.set_engine("whisper")
        self.control.set_whisper("small")
        self.assertEqual(self.recognizer.calls, [("whisper", base, "ru"), ("whisper", small, "ru")])

    def test_the_same_model_is_not_loaded_again(self):
        self.install("base")
        self.control.set_whisper("base")
        self.control.set_engine("whisper")
        self.control.set_whisper("base")
        self.assertEqual(len(self.recognizer.calls), 1)

    def test_a_new_language_reloads_whisper_and_the_same_one_does_not(self):
        folder = self.install("base")
        self.control.set_whisper("base")
        self.control.set_engine("whisper")
        self.control.set_whisper(language="en")
        self.control.set_whisper(language="en")
        self.assertEqual(self.recognizer.calls, [("whisper", folder, "ru"), ("whisper", folder, "en")])

    def test_back_to_vosk(self):
        self.install("base")
        self.control.set_whisper("base")
        self.control.set_engine("whisper")
        self.control.set_engine("vosk")
        self.assertEqual(self.recognizer.calls[-1][0], "vosk")
        self.assertEqual(self.make().settings.engine, "vosk")
        self.assertEqual(self.make().settings.whisper_model, "base", "the model stays chosen for the next time")

    def test_the_chosen_model_that_is_deleted_gives_the_commands_back_to_vosk(self):
        self.install("base")
        self.control.set_whisper("base")
        self.control.set_engine("whisper")
        self.control.remove_model("base")
        self.assertEqual(self.recognizer.calls[-1], ("vosk", None, "ru"))
        self.assertEqual((self.control.settings.engine, self.control.settings.whisper_model), ("vosk", None))
        self.assertFalse(self.control.library.is_installed("base"))

    def test_another_model_that_is_deleted_changes_nothing_else(self):
        self.install("base")
        self.install("small")
        self.control.set_whisper("base")
        self.control.set_engine("whisper")
        calls = len(self.recognizer.calls)
        self.control.remove_model("small")
        self.assertEqual(len(self.recognizer.calls), calls)
        self.assertEqual((self.control.settings.engine, self.control.settings.whisper_model), ("whisper", "base"))

    def test_a_model_that_is_not_there_cannot_be_deleted(self):
        with self.assertRaises(SttError):
            self.control.remove_model("base")

    def test_info_tells_what_the_page_shows(self):
        self.install("small")
        self.install("base")
        self.control.set_whisper("small")
        self.control.set_engine("whisper")
        info = self.control.info()
        self.assertEqual(info, {"engine": "whisper", "active": "whisper", "whisper_state": "loading",
                                "whisper_model": "small", "language": "ru", "installed": ["base", "small"]})


if __name__ == "__main__":
    unittest.main()
