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


class FakeSpeaker:
    def __init__(self):
        self.said = []

    def speak(self, text):
        self.said.append(text)


class FakeProductivity:
    def __init__(self, answer=None, app=None):
        self.answer, self.app, self.seen = answer, app, []

    def handle(self, query):
        self.seen.append(query)
        return self.answer

    def open_app(self, query):
        return self.app


def make_command_assistant(answer=None, app=None):
    assistant = make_assistant()
    assistant.process_command = Assistant.process_command.__get__(assistant)
    assistant.commands = []
    assistant.tts = FakeSpeaker()
    assistant.productivity = FakeProductivity(answer, app)
    assistant.chat_gpt = types.SimpleNamespace(ask=lambda q: f"нейросеть: {q}", model_name="test-llm")
    return assistant


class CommandOrderTests(unittest.TestCase):
    def test_timers_and_tasks_are_answered_first(self):
        assistant = make_command_assistant(answer="Поставила таймер")
        assistant.process_command("таймер на пять минут")
        self.assertEqual(assistant.tts.said, ["Поставила таймер"])

    def test_a_program_is_opened_before_asking_the_model(self):
        assistant = make_command_assistant(app="Открываю Калькулятор")
        assistant.process_command("открой калькулятор")
        self.assertEqual(assistant.tts.said, ["Открываю Калькулятор"])

    def test_everything_else_goes_to_the_language_model(self):
        assistant = make_command_assistant()
        assistant.process_command("расскажи анекдот")
        self.assertEqual(assistant.tts.said, ["нейросеть: расскажи анекдот"])

    def test_a_failure_in_the_planner_does_not_break_the_command(self):
        assistant = make_command_assistant()

        def boom(_query):
            raise RuntimeError("fail")

        assistant.productivity.handle = boom
        assistant.process_command("привет")
        self.assertEqual(assistant.tts.said, ["нейросеть: привет"])


class PlannerNotificationTests(unittest.TestCase):
    def test_texts_of_fired_items(self):
        describe = Assistant.describe_fired
        self.assertEqual(describe({"kind": "timer", "text": "", "length": 300, "due": "2026-10-28T08:00:00"}),
                         "Таймер на 5 минут сработал.")
        self.assertEqual(describe({"kind": "timer", "text": "чай", "length": 90, "due": "2026-10-28T08:00:00"}),
                         "Таймер на 1 минуту 30 секунд сработал: чай.")
        self.assertEqual(describe({"kind": "alarm", "text": "", "due": "2026-10-28T07:30:00"}),
                         "Будильник! Уже 07:30.")
        self.assertEqual(describe({"kind": "reminder", "text": "позвонить маме", "due": "2026-10-28T07:30:00"}),
                         "Напоминаю: позвонить маме.")
        self.assertTrue(describe({"kind": "reminder", "text": "х", "missed": True, "due": "x"}).startswith(
            "Напоминаю, пропущено"))

    def test_the_text_field_keeps_the_colon_of_a_clock_time(self):
        assistant = make_assistant()
        assistant.productivity = FakeProductivity(answer="ок")
        self.assertEqual(assistant.quick_add("Будильник на 7:30, пожалуйста!"), "ок")
        self.assertEqual(assistant.productivity.seen, ["будильник на 7:30 пожалуйста"])
        assistant.handle_text("Напомни в 18:00: позвонить")
        assistant.speech_recognizer.tasks.get_nowait()()
        self.assertEqual(assistant.processed[-1], "напомни в 18:00 позвонить")


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
