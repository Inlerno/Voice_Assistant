"""The words of a command that is being said: the recognizer passes them on while the phrase is recognized.

Vosk and the microphone are replaced by scripts, so the tests need neither a model nor a sound card.
"""
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import sounddevice  # noqa: F401
except Exception:                                           # PortAudio is not there (a build server)
    stub = types.ModuleType("sounddevice")
    stub.default = types.SimpleNamespace(device=(1, 1))
    stub.query_devices = lambda *args, **kwargs: {"default_samplerate": 16000}
    sys.modules["sounddevice"] = stub

from src.speech import speech_module as sm  # noqa: E402

SAMPLERATE = 16000


class ScriptedRecognizer:
    """What Vosk does with the blocks of a phrase: for every block of audio the next step of the script.

    A step is ("partial", text) - the phrase goes on, or ("final", text) - Vosk has found the end of the phrase."""

    instances = []

    def __init__(self, script):
        self.script = list(script)
        self.step = None
        self.partial_requests = 0
        self.heard = ""

    def AcceptWaveform(self, data):
        self.step = self.script.pop(0) if self.script else ("partial", self.heard)
        kind, text = self.step
        self.heard = text
        return kind == "final"

    def PartialResult(self):
        self.partial_requests += 1
        return json.dumps({"partial": self.heard})

    def Result(self):
        return json.dumps({"text": self.heard})

    def FinalResult(self):
        # What was heard so far when the recording stops in the middle of a phrase; it is not empty only if the
        # recognizer was sure about it (a script can say it was not by finishing with ("partial", "") steps)
        return json.dumps({"text": self.heard if self.script_sure else ""})

    script_sure = True


class FakeStream:
    """A microphone that has recorded one block for every step of the script before the stream is opened."""

    blocks = 0

    def __init__(self, samplerate, blocksize, device, dtype, channels, callback):
        self.callback = callback
        self.blocksize = blocksize

    def __enter__(self):
        for _ in range(FakeStream.blocks):
            self.callback(bytes(self.blocksize * 2), self.blocksize, None, None)
        return self

    def __exit__(self, *exc):
        return False


def make_recognizer(script, sure=True):
    """A recognizer whose Vosk follows the script and whose microphone has a block for every step."""
    fake_vosk = types.SimpleNamespace(
        Model=lambda path: object(),
        KaldiRecognizer=lambda model, rate, *args: recognizer_of(script, sure),
    )
    fake_sd = types.SimpleNamespace(default=types.SimpleNamespace(device=(0, 0)),
                                    query_devices=lambda *a, **k: {"default_samplerate": SAMPLERATE},
                                    RawInputStream=FakeStream)
    FakeStream.blocks = len(script)
    patches = [mock.patch.object(sm, "vosk", fake_vosk), mock.patch.object(sm, "sd", fake_sd)]
    for patch in patches:
        patch.start()
    recognizer = sm.SpeechRecognizer("model", input_device=0, profile=object(), big_model_path=None)
    return recognizer, patches


def recognizer_of(script, sure):
    rec = ScriptedRecognizer(script)
    rec.script_sure = sure
    ScriptedRecognizer.instances.append(rec)
    return rec


class LiveSpeechTests(unittest.TestCase):
    def setUp(self):
        ScriptedRecognizer.instances.clear()
        self.log = []

    def listen(self, script, live=True, sure=True, **options):
        recognizer, patches = make_recognizer(script, sure)
        for patch in patches:
            self.addCleanup(patch.stop)
        if live:
            recognizer.on_partial = lambda text: self.log.append(("partial", text))
        recognizer.on_phrase = lambda text, stats: self.log.append(("phrase", text))
        recognizer.on_state = lambda state: self.log.append(("state", state))
        return recognizer.listen_command(**options)

    def test_the_words_are_passed_on_while_the_phrase_is_said_and_go_away_after_the_final_text(self):
        result = self.listen([("partial", ""), ("partial", "поставь"), ("partial", "поставь таймер"),
                              ("partial", "поставь таймер на пять минут"), ("final", "поставь таймер на пять минут")])
        self.assertEqual(result, "поставь таймер на пять минут")
        self.assertEqual(self.log, [
            ("state", "listening"),
            ("partial", "поставь"),
            ("partial", "поставь таймер"),
            ("partial", "поставь таймер на пять минут"),
            ("phrase", "поставь таймер на пять минут"),
            ("state", "processing"),
            # the interface drops the draft only after the message of the phrase is queued, so it is not seen to blink
            ("partial", ""),
        ])

    def test_the_same_words_are_not_passed_twice(self):
        self.listen([("partial", "привет"), ("partial", "привет"), ("partial", "привет мир"), ("final", "привет мир")])
        self.assertEqual([text for kind, text in self.log if kind == "partial"], ["привет", "привет мир", ""])

    def test_a_guess_that_the_recognizer_takes_back_does_not_make_the_line_blink(self):
        # Vosk heard "да" in a breath and then decided it was nothing: the words are replaced, never cleared midway
        self.listen([("partial", "да"), ("partial", ""), ("partial", "добавь"), ("partial", "добавь задачу"),
                     ("final", "добавь задачу")])
        self.assertEqual([text for kind, text in self.log if kind == "partial"], ["да", "добавь", "добавь задачу", ""])

    def test_a_phrase_that_stops_in_the_middle_gives_what_was_heard_and_clears_the_words(self):
        result = self.listen([("partial", "включи"), ("partial", "включи музыку")], max_duration=0.4)
        self.assertEqual(result, "включи музыку")
        self.assertEqual(self.log[-3:], [("phrase", "включи музыку"), ("state", "processing"), ("partial", "")])

    def test_words_that_come_to_nothing_are_taken_away(self):
        # Noise: something was heard for a moment, but the recognizer is not sure about it in the end
        result = self.listen([("partial", "и"), ("partial", "")], max_duration=0.4, sure=False)
        self.assertIsNone(result)
        self.assertEqual([entry for entry in self.log if entry[0] != "state"], [("partial", "и"), ("partial", "")])

    def test_silence_passes_nothing_on(self):
        result = self.listen([("partial", "")] * 6, no_speech_timeout=0.4)
        self.assertIsNone(result)
        self.assertEqual([entry for entry in self.log if entry[0] != "state"], [])

    def test_with_the_switch_off_the_partial_text_is_not_even_asked_for_once_the_speech_has_begun(self):
        self.listen([("partial", ""), ("partial", "один"), ("partial", "один два"), ("partial", "один два три"),
                     ("final", "один два три")], live=False)
        recognizer = ScriptedRecognizer.instances[-1]
        # Only until the first word: that is how the recognizer tells that somebody is speaking
        self.assertEqual(recognizer.partial_requests, 2)
        self.assertEqual([entry for entry in self.log if entry[0] == "partial"], [])

    def test_with_the_switch_on_every_block_is_asked_for_its_words(self):
        self.listen([("partial", ""), ("partial", "один"), ("partial", "один два"), ("final", "один два")])
        self.assertEqual(ScriptedRecognizer.instances[-1].partial_requests, 3)

    def test_a_broken_callback_does_not_break_the_recognition(self):
        recognizer, patches = make_recognizer([("partial", "раз"), ("final", "раз два")])
        for patch in patches:
            self.addCleanup(patch.stop)

        def broken(text):
            raise RuntimeError("the interface is gone")

        recognizer.on_partial = broken
        with self.assertLogs(sm.logger, level="ERROR"):
            self.assertEqual(recognizer.listen_command(), "раз два")

    def test_a_phrase_from_a_chat_has_no_live_words(self):
        recognizer, patches = make_recognizer([])
        for patch in patches:
            self.addCleanup(patch.stop)
        recognizer.on_partial = lambda text: self.log.append(("partial", text))
        with recognizer.command_from(lambda: "включи свет"):
            self.assertEqual(recognizer.listen_command(), "включи свет")
        self.assertEqual(self.log, [])


if __name__ == "__main__":
    unittest.main()
