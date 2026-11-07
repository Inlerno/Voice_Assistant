"""A REAL Whisper model hears the recordings of commands. See tests/run_whisper_e2e.py.

Skipped when there is no Whisper model on this computer: download one in "Настройки → Распознавание" (it goes to
data/stt/whisper) or point ASTRA_WHISPER_MODEL to the folder of a model.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

from rapidfuzz import fuzz

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RUNNER = HERE / "run_whisper_e2e.py"

SAID = {
    "timer": "поставь таймер на пять минут",
    "tasks": "добавь задачу купить молоко",
    "shutdown": "выключи компьютер",
}
# The models that are good enough to be asked to understand all three phrases; the tiny one is not
RELIABLE = {"base", "small", "turbo", "large-v3"}


def command_for(spoken):
    """The command of src/config/commands.json that a phrase is closest to, as the assistant picks it."""
    commands = json.loads((ROOT / "src" / "config" / "commands.json").read_text(encoding="utf-8"))["commands"]
    best, best_score = None, 0
    for command in commands:
        for trigger in command["triggers"]:
            score = fuzz.ratio(spoken, trigger)
            if score > best_score:
                best, best_score = command, score
    return best if best_score > 75 else None


class WhisperEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The python of the tests, in a mode that ignores the environment: nothing but this program and its libraries
        done = subprocess.run([sys.executable, "-I", str(RUNNER)], capture_output=True, text=True, encoding="utf-8",
                              timeout=900)
        if done.returncode == 77:
            raise unittest.SkipTest("нет модели Whisper (скачайте её в Настройки → Распознавание), "
                                    "sherpa-onnx или модели Vosk в src/assets")
        if done.returncode != 0:
            raise AssertionError(f"сценарий завершился с кодом {done.returncode}:\n{done.stderr[-3000:]}\n"
                                 f"{done.stdout[-1000:]}")
        cls.result = json.loads(done.stdout.strip().splitlines()[-1])
        cls.reliable = cls.result["model"] in RELIABLE

    def test_the_model_loads_and_becomes_the_engine_of_the_recognizer(self):
        self.assertEqual(self.result["recognizer_engine"], "whisper")
        self.assertGreater(self.result["load_s"], 0)

    def test_every_recording_gives_a_text_in_the_form_of_a_spoken_command(self):
        for name in SAID:
            spoken = self.result["engine"][name]["spoken"]
            with self.subTest(recording=name):
                self.assertTrue(spoken, "Whisper heard nothing in a recording of a voice")
                self.assertEqual(spoken, spoken.lower())
                self.assertNotRegex(spoken, r"[.,!?«»]")

    def test_the_commands_are_understood(self):
        if not self.reliable:
            self.skipTest(f"модель {self.result['model']} слишком слабая, чтобы требовать точного текста")
        timer = self.result["engine"]["timer"]["effect"]
        self.assertEqual(timer["timers"], [300], timer)
        tasks = self.result["engine"]["tasks"]["effect"]
        self.assertEqual(tasks["tasks"], ["купить молоко"], tasks)
        shutdown = command_for(self.result["engine"]["shutdown"]["spoken"])
        self.assertEqual(shutdown and shutdown["make"], "shutdown", self.result["engine"]["shutdown"])

    def test_a_voice_message_is_recognized_by_the_engine_that_is_chosen(self):
        for name in SAID:
            heard = self.result["transcribe"][name]
            with self.subTest(recording=name):
                self.assertTrue(heard["text"])
                self.assertEqual(heard["stats"]["engine"], f"Whisper {self.result['model']}")
                self.assertGreater(heard["stats"]["decode_ms"], 0)
                # the same text as the engine writes by itself
                self.assertEqual(heard["text"], self.result["engine"][name]["spoken"])

    def test_the_words_show_up_while_the_phrase_is_said_and_whisper_writes_the_message(self):
        for name in SAID:
            listen = self.result["listen"][name]
            events = listen["events"]
            kinds = [event[0] for event in events]
            with self.subTest(recording=name):
                # Vosk shows the words one after another, then Whisper works on the whole phrase
                shown = [event[1] for event in events if event[0] == "partial" and event[1]]
                self.assertGreaterEqual(len(shown), 2, shown)
                self.assertEqual(kinds.count("phrase"), 1)
                phrase = next(event for event in events if event[0] == "phrase")
                self.assertEqual(phrase[1], listen["result"])
                self.assertEqual(phrase[2]["engine"], f"Whisper {self.result['model']}")
                # the state is "processing" while Whisper thinks, which is before the message
                before = kinds[:kinds.index("phrase")]
                self.assertEqual(before[-1], "state")
                self.assertEqual(events[kinds.index("phrase") - 1], ["state", "processing"])
                # the live text goes away after the message
                self.assertEqual(events[-1], ["partial", ""])
                self.assertEqual(listen["result"], self.result["engine"][name]["spoken"])

    def test_recognition_is_fast_enough_for_a_dialog(self):
        for name in SAID:
            audio_s = self.result["engine"][name]["audio_s"]
            spent = self.result["engine"][name]["ms"] / 1000
            with self.subTest(recording=name):
                # a loose limit: the model must not need more than ten seconds for two seconds of speech even on
                # a slow computer (the tiny and base models need less than a second on a modern one)
                self.assertLess(spent, max(10.0, audio_s * 5), f"{spent:.1f} с на {audio_s:.1f} с речи")

    def test_vosk_and_whisper_agree_on_the_words_of_the_phrase(self):
        if not self.reliable:
            self.skipTest("слабая модель")
        for name, said in SAID.items():
            listen = self.result["listen"][name]
            last_live = [event[1] for event in listen["events"] if event[0] == "partial" and event[1]][-1]
            with self.subTest(recording=name):
                self.assertEqual(last_live, said)
                self.assertGreater(fuzz.ratio(listen["result"], said), 85)


if __name__ == "__main__":
    unittest.main()
