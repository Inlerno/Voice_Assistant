"""A command heard by the real Vosk model shows up word by word while it is being said. See tests/run_live_speech_e2e.py."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER = HERE / "run_live_speech_e2e.py"

SAID = {
    "timer": "поставь таймер на пять минут",
    "tasks": "добавь задачу купить молоко",
    "shutdown": "выключи компьютер",
}


class LiveSpeechEndToEndTests(unittest.TestCase):
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

    def partials(self, name):
        return [text for kind, text in self.result[name]["events"] if kind == "partial"]

    def test_the_command_is_recognized_as_it_was_said(self):
        for name, said in SAID.items():
            self.assertEqual(self.result[name]["result"], said, name)

    def test_the_words_come_one_after_another_before_the_phrase_is_final(self):
        for name, said in SAID.items():
            events = self.result[name]["events"]
            shown = [text for kind, text in events if kind == "partial" and text]
            self.assertGreaterEqual(len(shown), 2, f"{name}: the words must grow, not come at once: {shown}")
            # everything that was shown before the final text is only a part of the phrase, the last one is all of it
            phrase_at = [kind for kind, _ in events].index("phrase")
            before = [text for kind, text in events[:phrase_at] if kind == "partial"]
            self.assertEqual(before[-1], said, name)
            self.assertTrue(all(said.startswith(text) or text in said for text in before[1:]), f"{name}: {before}")

    def test_the_phrase_is_reported_once_and_the_live_text_is_cleared_after_it(self):
        for name in SAID:
            events = self.result[name]["events"]
            self.assertEqual([kind for kind, _ in events].count("phrase"), 1, name)
            self.assertEqual(events[-1], ["partial", ""], f"{name}: the draft must go away after the message")


if __name__ == "__main__":
    unittest.main()
