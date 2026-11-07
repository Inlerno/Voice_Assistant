"""Whisper in the recognizer: it takes over the commands once its model is loaded, Vosk finds the end of the phrase.

Vosk, Whisper and the microphone are replaced by scripts, so the tests need neither a model nor a sound card.
"""
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_live_speech import SAMPLERATE, FakeStream, ScriptedRecognizer  # noqa: E402

from src.speech import speech_module as sm  # noqa: E402
from src.speech.whisper_engine import MODEL_FILES  # noqa: E402

BLOCK_BYTES = int(SAMPLERATE * 0.2) * 2


class FakeWhisper:
    """The loaded Whisper as the recognizer sees it: it is given a recording and answers with a text."""

    def __init__(self, text="Поставь таймер на 5 минут.", name="base", fail=False):
        self.text, self.name, self.fail = text, name, fail
        self.calls = []

    def transcribe(self, pcm, rate):
        self.calls.append((len(pcm), rate))
        if self.fail:
            raise RuntimeError("out of memory")
        return self.text


class Setup:
    """A recognizer with a scripted Vosk (it remembers which model every recognizer was made with)."""

    def __init__(self, test, script, whisper=None, sure=True):
        self.made_with = []

        def kaldi(model, rate, *args):
            self.made_with.append(model)
            rec = ScriptedRecognizer(script)
            rec.script_sure = sure
            return rec

        fake_vosk = types.SimpleNamespace(Model=lambda path: "small model", KaldiRecognizer=kaldi)
        fake_sd = types.SimpleNamespace(default=types.SimpleNamespace(device=(0, 0)),
                                        query_devices=lambda *a, **k: {"default_samplerate": SAMPLERATE},
                                        RawInputStream=FakeStream)
        FakeStream.blocks = len(script)
        for patch in (mock.patch.object(sm, "vosk", fake_vosk), mock.patch.object(sm, "sd", fake_sd)):
            patch.start()
            test.addCleanup(patch.stop)
        self.recognizer = sm.SpeechRecognizer("model", input_device=0, profile=object(), big_model_path=None)
        # The big model recognizes the commands of Vosk; the small one is the one that watches for the end of a phrase
        self.recognizer.command_model = "big model"
        if whisper is not None:
            self.recognizer.whisper, self.recognizer.engine = whisper, "whisper"
        self.log = []
        self.recognizer.on_state = lambda state: self.log.append(("state", state))
        self.recognizer.on_phrase = lambda text, stats: self.log.append(("phrase", text, stats))
        self.recognizer.on_partial = lambda text: self.log.append(("partial", text))
        self.recognizer.on_engine = lambda text: self.log.append(("engine", text))

    def phrases(self):
        return [entry for entry in self.log if entry[0] == "phrase"]


PHRASE = [("partial", ""), ("partial", "поставь"), ("partial", "поставь таймер"), ("final", "поставь таймер")]


class ListenWithWhisperTests(unittest.TestCase):
    def test_the_text_of_whisper_replaces_the_one_of_vosk(self):
        whisper = FakeWhisper()
        setup = Setup(self, PHRASE, whisper)
        # Whisper writes numbers and punctuation; the command goes on as a spoken one
        self.assertEqual(setup.recognizer.listen_command(), "поставь таймер на 5 минут")
        (_, text, stats), = setup.phrases()
        self.assertEqual(text, "поставь таймер на 5 минут")
        self.assertEqual(stats["engine"], "Whisper base")
        self.assertEqual(len(whisper.calls), 1)

    def test_vosk_with_the_small_model_watches_the_microphone_and_shows_the_words(self):
        setup = Setup(self, PHRASE, FakeWhisper())
        setup.recognizer.listen_command()
        self.assertEqual(setup.made_with, ["small model"])
        self.assertEqual([entry[1] for entry in setup.log if entry[0] == "partial"],
                         ["поставь", "поставь таймер", ""])

    def test_the_state_is_processing_while_whisper_thinks_and_before_the_message(self):
        setup = Setup(self, PHRASE, FakeWhisper())
        setup.recognizer.listen_command()
        kinds = [(entry[0], entry[1] if entry[0] == "state" else None) for entry in setup.log]
        self.assertLess(kinds.index(("state", "processing")), kinds.index(("phrase", None)))

    def test_the_recording_starts_a_little_before_the_first_words(self):
        # Eight quiet blocks, then speech from the ninth: Whisper gets the last four quiet ones and all the rest
        script = [("partial", "")] * 8 + [("partial", "поставь"), ("partial", "поставь таймер"),
                                          ("partial", "поставь таймер на"), ("final", "поставь таймер на пять")]
        whisper = FakeWhisper()
        Setup(self, script, whisper).recognizer.listen_command(max_duration=10, no_speech_timeout=10)
        self.assertEqual(whisper.calls, [((len(script) - 4) * BLOCK_BYTES, SAMPLERATE)])

    def test_a_phrase_that_begins_at_once_is_given_whole(self):
        whisper = FakeWhisper()
        Setup(self, [("partial", "привет"), ("final", "привет")], whisper).recognizer.listen_command()
        self.assertEqual(whisper.calls, [(2 * BLOCK_BYTES, SAMPLERATE)])

    def test_the_phrase_is_cut_by_the_time_limit_and_whisper_still_gets_it(self):
        script = [("partial", "расскажи"), ("partial", "расскажи про"), ("partial", "расскажи про погоду")]
        whisper = FakeWhisper(text="Расскажи про погоду")
        setup = Setup(self, script, whisper)
        self.assertEqual(setup.recognizer.listen_command(max_duration=0.6), "расскажи про погоду")
        self.assertEqual(whisper.calls, [(3 * BLOCK_BYTES, SAMPLERATE)])

    def test_noise_that_whisper_makes_up_is_no_phrase(self):
        whisper = FakeWhisper(text="Субтитры сделал DimaTorzok")
        setup = Setup(self, PHRASE, whisper)
        with self.assertLogs("src.speech.speech_module", level="INFO"):
            self.assertIsNone(setup.recognizer.listen_command())
        self.assertEqual(setup.phrases(), [])
        self.assertEqual(setup.log[-1], ("partial", ""), "the words on the screen must go away")

    def test_a_text_of_nothing_is_no_phrase_either(self):
        setup = Setup(self, PHRASE, FakeWhisper(text=" . "))
        self.assertIsNone(setup.recognizer.listen_command())
        self.assertEqual(setup.phrases(), [])

    def test_when_whisper_breaks_the_words_of_vosk_are_kept(self):
        whisper = FakeWhisper(fail=True)
        setup = Setup(self, PHRASE, whisper)
        with self.assertLogs("src.speech.speech_module", level="ERROR"):
            result = setup.recognizer.listen_command()
        self.assertEqual(result, "поставь таймер")
        self.assertIsNone(setup.phrases()[0][2]["engine"])

    def test_whisper_is_not_asked_when_nobody_spoke(self):
        whisper = FakeWhisper()
        setup = Setup(self, [("partial", "")] * 10, whisper)
        self.assertIsNone(setup.recognizer.listen_command(max_duration=2, no_speech_timeout=1))
        self.assertEqual(whisper.calls, [])

    def test_without_whisper_vosk_recognizes_with_the_big_model_and_no_recording_is_needed(self):
        setup = Setup(self, PHRASE)
        self.assertEqual(setup.recognizer.listen_command(), "поставь таймер")
        self.assertEqual(setup.made_with, ["big model"])
        self.assertIsNone(setup.phrases()[0][2]["engine"])

    def test_a_model_that_is_still_loading_changes_nothing(self):
        setup = Setup(self, PHRASE)
        setup.recognizer.whisper_state = "loading"
        self.assertEqual(setup.recognizer.listen_command(), "поставь таймер")
        self.assertEqual(setup.made_with, ["big model"])


class TranscribeWithWhisperTests(unittest.TestCase):
    def test_a_voice_message_goes_to_whisper(self):
        whisper = FakeWhisper(text="Выключи компьютер.")
        setup = Setup(self, [], whisper)
        text, stats = setup.recognizer.transcribe(bytes(16000 * 2 * 3), 16000)
        self.assertEqual(text, "выключи компьютер")
        self.assertEqual(stats["engine"], "Whisper base")
        self.assertAlmostEqual(stats["audio_s"], 3.0)
        self.assertEqual(whisper.calls, [(16000 * 2 * 3, 16000)])
        self.assertEqual(setup.made_with, [], "Vosk is not needed when Whisper recognizes")

    def test_silence_of_the_recording_gives_an_empty_text(self):
        setup = Setup(self, [], FakeWhisper(text="Продолжение следует..."))
        text, _ = setup.recognizer.transcribe(bytes(16000 * 2), 16000)
        self.assertEqual(text, "")

    def test_a_broken_whisper_hands_the_recording_to_vosk(self):
        setup = Setup(self, [("partial", "добавь задачу купить молоко")], FakeWhisper(fail=True))
        with self.assertLogs("src.speech.speech_module", level="ERROR"):
            text, stats = setup.recognizer.transcribe(bytes(16000 * 2), 16000)
        self.assertEqual(text, "добавь задачу купить молоко")
        self.assertEqual(setup.made_with, ["big model"])
        self.assertNotIn("engine", stats)

    def test_vosk_recognizes_when_whisper_is_not_chosen(self):
        setup = Setup(self, [("partial", "привет")])
        text, stats = setup.recognizer.transcribe(bytes(16000 * 2), 16000)
        self.assertEqual(text, "привет")
        self.assertEqual(setup.made_with, ["big model"])
        self.assertNotIn("engine", stats)


def model_folder(test, name="base"):
    root = tempfile.TemporaryDirectory()
    test.addCleanup(root.cleanup)
    folder = Path(root.name) / name
    folder.mkdir()
    for file in MODEL_FILES:
        (folder / file).write_bytes(b"x")
    return folder


class LoadingTests(unittest.TestCase):
    def setUp(self):
        self.setup = Setup(self, [])
        self.recognizer = self.setup.recognizer
        self.told = [entry[1] for entry in self.setup.log if entry[0] == "engine"]

    def messages(self):
        return [entry[1] for entry in self.setup.log if entry[0] == "engine"]

    def test_vosk_goes_on_while_whisper_loads_and_whisper_takes_over_when_it_is_ready(self):
        gate = threading.Event()
        seen = []

        def factory(encoder, decoder, tokens, language, threads):
            seen.append((Path(encoder).name, Path(decoder).name, Path(tokens).name, language, threads))
            gate.wait(5)
            return object()

        thread = self.recognizer.use_engine("whisper", model_folder(self), "ru", 3, factory)
        self.assertEqual((self.recognizer.engine, self.recognizer.whisper_state), ("vosk", "loading"))
        self.assertEqual(self.recognizer.engine_name, "Vosk")
        gate.set()
        thread.join(5)
        self.assertEqual((self.recognizer.engine, self.recognizer.whisper_state), ("whisper", "ready"))
        self.assertEqual(self.recognizer.engine_name, "Whisper base")
        self.assertEqual(seen, [("encoder.onnx", "decoder.onnx", "tokens.txt", "ru", 3)])
        self.assertEqual(len(self.messages()), 2)
        self.assertIn("Загружаю Whisper base", self.messages()[0])
        self.assertIn("готов", self.messages()[1])

    def test_a_model_that_cannot_be_loaded_leaves_vosk_and_says_so(self):
        def factory(*args):
            raise RuntimeError("not enough memory")

        with self.assertLogs("src.speech.speech_module", level="ERROR"):
            self.recognizer.use_engine("whisper", model_folder(self), "ru", None, factory).join(5)
        self.assertEqual((self.recognizer.engine, self.recognizer.whisper_state), ("vosk", "failed"))
        self.assertIsNone(self.recognizer.whisper)
        self.assertIn("не удалось", self.messages()[-1])

    def test_a_folder_with_missing_files_leaves_vosk(self):
        folder = model_folder(self)
        (folder / "decoder.onnx").unlink()
        with self.assertLogs("src.speech.speech_module", level="ERROR"):
            self.recognizer.use_engine("whisper", folder, "ru", None, lambda *a: object()).join(5)
        self.assertEqual((self.recognizer.engine, self.recognizer.whisper_state), ("vosk", "failed"))

    def test_no_model_chosen_leaves_vosk(self):
        self.assertIsNone(self.recognizer.use_engine("whisper", None))
        self.assertEqual((self.recognizer.engine, self.recognizer.whisper_state), ("vosk", "failed"))
        self.assertIn("не выбрана", self.messages()[-1])

    def test_back_to_vosk_drops_the_loaded_model(self):
        self.recognizer.use_engine("whisper", model_folder(self), "ru", None, lambda *a: object()).join(5)
        self.assertEqual(self.recognizer.engine, "whisper")
        self.assertIsNone(self.recognizer.use_engine("vosk"))
        self.assertEqual((self.recognizer.engine, self.recognizer.whisper, self.recognizer.whisper_state),
                         ("vosk", None, "none"))

    def test_vosk_chosen_while_the_model_loads_wins_when_the_model_is_ready(self):
        gate = threading.Event()
        thread = self.recognizer.use_engine("whisper", model_folder(self), "ru", None,
                                            lambda *a: (gate.wait(5), object())[1])
        self.recognizer.use_engine("vosk")
        gate.set()
        thread.join(5)
        self.assertEqual((self.recognizer.engine, self.recognizer.whisper), ("vosk", None))

    def test_the_last_chosen_model_wins_even_if_it_is_loaded_first(self):
        first_gate = threading.Event()
        first = self.recognizer.use_engine("whisper", model_folder(self, "small"), "ru", None,
                                           lambda *a: (first_gate.wait(5), "small")[1])
        second = self.recognizer.use_engine("whisper", model_folder(self, "base"), "ru", None, lambda *a: "base")
        second.join(5)
        first_gate.set()
        first.join(5)
        self.assertEqual(self.recognizer.whisper.name, "base")
        self.assertEqual(self.recognizer.engine_name, "Whisper base")

    def test_a_failed_old_load_does_not_spoil_the_new_one(self):
        gate = threading.Event()

        def slow_failure(*args):
            gate.wait(5)
            raise RuntimeError("late failure")

        with self.assertLogs("src.speech.speech_module", level="ERROR"):
            first = self.recognizer.use_engine("whisper", model_folder(self, "small"), "ru", None, slow_failure)
            self.recognizer.use_engine("whisper", model_folder(self, "base"), "ru", None, lambda *a: "base").join(5)
            gate.set()
            first.join(5)
        self.assertEqual((self.recognizer.engine, self.recognizer.whisper_state), ("whisper", "ready"))

    def test_a_callback_that_fails_does_not_break_the_loading(self):
        def broken(text):
            raise RuntimeError("the window is gone")

        self.recognizer.on_engine = broken
        with self.assertLogs("src.speech.speech_module", level="ERROR"):
            self.recognizer.use_engine("whisper", model_folder(self), "ru", None, lambda *a: object()).join(5)
        self.assertEqual(self.recognizer.engine, "whisper")


if __name__ == "__main__":
    unittest.main()
