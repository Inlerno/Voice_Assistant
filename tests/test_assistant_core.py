"""The parts of the assistant that the interface uses: typed commands, the microphone switch, the model info."""
import queue
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The heavy dependencies (audio, speech, interface) are not needed to test the logic of the core. They are replaced
# only while the module is imported, so the other test files keep working with the real libraries
_STUBBED = ("pygame", "sounddevice", "rapidfuzz", "lmstudio", "pyttsx3", "src.interface.interface", "src.network",
            "src.audio.sound_module", "src.speech.speech_module", "src.system.system_control",
            "src.speech.tts_module", "src.integrations.chat_gpt")
with mock.patch.dict(sys.modules, {name: mock.MagicMock() for name in _STUBBED}):
    from src.core.assistant_core import Assistant


class FakeRecognizer:
    def __init__(self):
        self.paused = False
        self.big_model_path = None
        self.big_model_state = "none"
        self.tasks = queue.Queue()

    def set_paused(self, paused):
        self.paused = paused

    def run_exclusive(self, func):
        self.tasks.put(func)
        return "future"


class FakeUI:
    def __init__(self):
        self.messages = []
        self.states = []

    def add_message(self, role, text, meta=None):
        self.messages.append((role, text, meta))

    def set_state(self, state):
        self.states.append(state)


def make_assistant():
    assistant = object.__new__(Assistant)
    assistant.speech_recognizer = FakeRecognizer()
    assistant.interface_manager = FakeUI()
    assistant._answer_started = None
    assistant.chat_gpt = types.SimpleNamespace(model_name="test-llm")
    assistant.processed = []
    assistant.process_command = assistant.processed.append
    return assistant


class TypedCommandTests(unittest.TestCase):
    def test_text_is_normalized_like_a_spoken_command(self):
        assistant = make_assistant()
        self.assertEqual(assistant.handle_text("  Включи,   МУЗЫКУ! "), "future")
        assistant.speech_recognizer.tasks.get_nowait()()
        self.assertEqual(assistant.processed, ["включи музыку"])

    def test_original_text_is_shown_in_the_dialog(self):
        assistant = make_assistant()
        assistant.handle_text("Включи музыку!")
        self.assertEqual(assistant.interface_manager.messages, [("user", "Включи музыку!", "введено текстом")])
        self.assertEqual(assistant.interface_manager.states, ["processing"])

    def test_empty_text_does_nothing(self):
        assistant = make_assistant()
        self.assertIsNone(assistant.handle_text("  ?! "))
        self.assertTrue(assistant.speech_recognizer.tasks.empty())
        self.assertEqual(assistant.interface_manager.messages, [])

    def test_state_is_restored_even_if_the_command_fails(self):
        assistant = make_assistant()

        def boom(_query):
            raise RuntimeError("fail")

        assistant.process_command = boom
        assistant.handle_text("сделай что-нибудь")
        assistant.speech_recognizer.tasks.get_nowait()()
        self.assertEqual(assistant.interface_manager.states[-1], "idle")

    def test_state_is_paused_when_the_microphone_is_off(self):
        assistant = make_assistant()
        assistant.set_listening(False)
        assistant.handle_text("привет")
        assistant.speech_recognizer.tasks.get_nowait()()
        self.assertEqual(assistant.interface_manager.states[-1], "paused")


class MicrophoneSwitchTests(unittest.TestCase):
    def test_switch(self):
        assistant = make_assistant()
        self.assertTrue(assistant.listening)
        assistant.set_listening(False)
        self.assertFalse(assistant.listening)
        self.assertTrue(assistant.speech_recognizer.paused)
        assistant.set_listening(True)
        self.assertTrue(assistant.listening)


class ModelsInfoTests(unittest.TestCase):
    def test_without_big_model(self):
        info = make_assistant().models_info()
        self.assertIsNone(info["big_model"])
        self.assertEqual(info["big_model_state"], "none")
        self.assertEqual(info["llm"], "test-llm")

    def test_with_big_model(self):
        assistant = make_assistant()
        assistant.speech_recognizer.big_model_path = "/models/vosk-model-ru-0.42"
        assistant.speech_recognizer.big_model_state = "ready"
        info = assistant.models_info()
        self.assertEqual(info["big_model"], "vosk-model-ru-0.42")
        self.assertEqual(info["big_model_state"], "ready")


if __name__ == "__main__":
    unittest.main()
