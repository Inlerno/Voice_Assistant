"""A voice message from Telegram through the real decoder and the real Vosk model (the small one that comes with the
program): what is said in the recording is what the assistant is asked to do. See tests/run_voice_e2e.py."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER = HERE / "run_voice_e2e.py"


class VoiceMessageEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The python of the tests, in a mode that ignores the environment: nothing but this program and its libraries
        done = subprocess.run([sys.executable, "-I", str(RUNNER)], capture_output=True, text=True, encoding="utf-8",
                              timeout=300)
        if done.returncode == 77:
            raise unittest.SkipTest("нет библиотеки vosk или модели распознавания в src/assets")
        if done.returncode != 0:
            raise AssertionError(f"сценарий завершился с кодом {done.returncode}:\n{done.stderr[-3000:]}\n{done.stdout[-1000:]}")
        cls.result = json.loads(done.stdout.strip().splitlines()[-1])

    def test_what_is_said_is_recognized_and_shown_to_the_person(self):
        heard = self.result["heard"]
        self.assertIn("🎙 «поставь таймер на пять минут»", heard["timer"])
        self.assertIn("🎙 «добавь задачу купить молоко»", heard["tasks"])
        self.assertIn("🎙 «выключи компьютер»", heard["shutdown"])

    def test_the_command_that_was_said_is_the_command_that_runs(self):
        self.assertEqual(self.result["ran"], ["поставь таймер на пять минут", "добавь задачу купить молоко",
                                              "выключи компьютер"])
        # what was heard and the answer come to the chat as one message
        self.assertEqual(self.result["answers"]["timer"],
                         "🎙 «поставь таймер на пять минут»\nВыполнено: поставь таймер на пять минут")

    def test_the_dialog_gets_a_note_about_the_voice_and_the_time_of_recognition(self):
        for note in self.result["notes"]:
            self.assertRegex(note, r"^голосовое · распознавание \d+ мс$")

    def test_a_dangerous_command_waits_for_the_tap_even_when_it_was_said(self):
        self.assertEqual(self.result["ran_before_the_tap"], ["поставь таймер на пять минут",
                                                              "добавь задачу купить молоко"])
        self.assertIn("выключит компьютер", self.result["heard"]["shutdown"])


if __name__ == "__main__":
    unittest.main()
