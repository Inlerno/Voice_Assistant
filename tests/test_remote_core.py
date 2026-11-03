"""What the assistant gives to a chat (Telegram): where its answers go, how its questions are asked, how a command is
checked before it runs and how a recording is recognized."""
import json
import queue
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

try:
    import sounddevice  # noqa: F401
except Exception:                                           # PortAudio is not there (a build server)
    stub = types.ModuleType("sounddevice")
    stub.default = types.SimpleNamespace(device=(1, 1))
    stub.query_devices = lambda *args, **kwargs: {"default_samplerate": 16000}
    sys.modules["sounddevice"] = stub

from rapidfuzz import fuzz as real_fuzz  # noqa: E402

from src.core.remote import RemoteHost, Verdict, normalize_query, show_bot_in_window  # noqa: E402
from src.speech import speech_module as sm  # noqa: E402
from src.speech import voices as V  # noqa: E402
from src.speech.tts_module import TTS  # noqa: E402
from test_assistant_core import Assistant, FakeUI, make_assistant  # noqa: E402


class FakeSystem:
    default_id = "sapi:one"

    def __init__(self):
        self.said = []

    def voices(self):
        return [V.Voice("sapi:one", "Голос", V.SYSTEM)]

    def say(self, text, voice_id, rate, volume, speaker=0):
        self.said.append(text)

    def stop(self):
        pass


class NoNeural:
    def available(self):
        return False

    def say(self, *args, **kwargs):
        pass

    def stop(self):
        pass


class Sink:
    """What a chat is for the assistant: it takes the phrases (say) and may ask for them to be said aloud too."""

    def __init__(self, aloud=False, answers=()):
        self.said, self.speak_aloud, self.label = [], aloud, "Telegram · Анна"
        self.answers = list(answers)
        self.asked = 0

    def say(self, text):
        self.said.append(text)

    def ask(self):
        self.asked += 1
        return self.answers.pop(0) if self.answers else None


def make_tts(folder):
    system = FakeSystem()
    tts = TTS(library=V.VoiceLibrary(root=Path(folder) / "voices"), settings=V.TtsSettings(Path(folder) / "tts.json"),
              system_engine_factory=lambda: system, neural_engine=NoNeural())
    return tts, system


class QueryTests(unittest.TestCase):
    def test_a_typed_phrase_becomes_a_spoken_one(self):
        cases = {
            "  Включи,   МУЗЫКУ! ": "включи музыку",
            "Будильник на 7:30, пожалуйста!": "будильник на 7:30 пожалуйста",
            "Напомни в 18:00: позвонить": "напомни в 18:00 позвонить",
            "Привет — как дела?": "привет как дела",
            "Таймер 5 мин.": "таймер 5 мин",
            "ёлка": "ёлка",
            "?!": "",
            "": "",
            None: "",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(normalize_query(text), expected)

    def test_the_core_uses_the_same_rule_for_typed_text(self):
        assistant = make_assistant()
        assistant.handle_text("Напомни в 18:00: позвонить")
        assistant.speech_recognizer.tasks.get_nowait()()
        self.assertEqual(assistant.processed, [normalize_query("Напомни в 18:00: позвонить")])


class VerdictTests(unittest.TestCase):
    def host(self, make):
        seen = []

        def match(query):
            seen.append(query)
            return {"make": make, "triggers": ["x"]} if make else None

        return RemoteHost(types.SimpleNamespace(match_command=match)), seen

    def test_dangerous_commands_are_marked_with_what_they_do(self):
        for make, effect in (("shutdown", "выключит компьютер"), ("restart", "перезагрузит компьютер"),
                             ("sleep", "переведёт компьютер в спящий режим"), ("empty", "очистит корзину")):
            with self.subTest(make=make):
                host, seen = self.host(make)
                verdict = host.check("Выключи компьютер!")
                self.assertEqual(verdict, Verdict("выключи компьютер", effect, False))
                self.assertEqual(seen, ["выключи компьютер"])

    def test_commands_that_need_the_screen_stay_on_the_computer(self):
        for make in ("hotkey", "keyboard_write", "finish"):
            with self.subTest(make=make):
                host, _ = self.host(make)
                self.assertTrue(host.check("закрой программу").local_only)

    def test_ending_the_assistant_is_both_dangerous_and_local(self):
        host, _ = self.host("finish")
        verdict = host.check("пока")
        self.assertTrue(verdict.local_only)
        self.assertEqual(verdict.effect, "завершит работу ассистента")

    def test_harmless_and_unknown_commands(self):
        host, _ = self.host("get_current_time")
        self.assertEqual(host.check("сколько времени"), Verdict("сколько времени", None, False))
        host, _ = self.host(None)
        self.assertEqual(host.check("расскажи анекдот"), Verdict("расскажи анекдот", None, False))

    def test_an_empty_phrase_is_not_looked_up(self):
        host, seen = self.host("shutdown")
        self.assertEqual(host.check(" ?! "), Verdict(""))
        self.assertEqual(seen, [])

    def test_run_and_transcribe_go_to_the_assistant(self):
        calls = []
        recognizer = types.SimpleNamespace(transcribe=lambda pcm, rate: calls.append((pcm, rate)) or ("текст", {}))
        assistant = types.SimpleNamespace(
            handle_remote=lambda text, io, note=None: calls.append((text, io, note)) or "future",
            speech_recognizer=recognizer)
        host = RemoteHost(assistant)
        self.assertEqual(host.run("привет", "io", "заметка"), "future")
        self.assertEqual(host.transcribe(b"\x00\x00", 16000), ("текст", {}))
        self.assertEqual(calls, [("привет", "io", "заметка"), (b"\x00\x00", 16000)])


class WindowWiringTests(unittest.TestCase):
    class Window:
        def __init__(self):
            self.calls = []

        def set_service(self, name, state):
            self.calls.append(("service", name, state))

        def telegram_changed(self):
            self.calls.append(("changed",))

        def notice(self, text):
            self.calls.append(("notice", text))

    def test_the_state_of_the_bot_lights_the_status_bar(self):
        window, bot = self.Window(), types.SimpleNamespace(state="off", on_status=None, on_change=None, on_notice=None)
        show_bot_in_window(bot, window)
        self.assertEqual(window.calls, [("service", "tg", "off")])
        window.calls.clear()
        for state, light in (("connecting", "busy"), ("ok", "ok"), ("error", "error"), ("off", "off")):
            bot.on_status(state, "text")
            self.assertEqual(window.calls, [("service", "tg", light), ("changed",)])
            window.calls.clear()
        bot.on_change()
        bot.on_notice("Новый аккаунт")
        self.assertEqual(window.calls, [("changed",), ("notice", "Новый аккаунт")])

    def test_a_bot_that_runs_already_shows_its_light(self):
        window = self.Window()
        show_bot_in_window(types.SimpleNamespace(state="ok"), window)
        self.assertEqual(window.calls, [("service", "tg", "ok")])


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.tts, self.system = make_tts(self.dir.name)
        self.shown = []
        self.tts.on_speak = self.shown.append

    def test_the_phrases_go_to_the_chat_and_not_to_the_speakers(self):
        sink = Sink()
        with self.tts.capture(sink):
            self.tts.speak("Таймер поставлен")
            self.tts.speak("**Готово**")
        self.assertEqual(sink.said, ["Таймер поставлен", "**Готово**"])
        self.assertEqual(self.system.said, [])
        self.assertEqual(self.shown, ["Таймер поставлен", "**Готово**"], "the window still shows what was said")

    def test_the_computer_can_say_it_too(self):
        sink = Sink(aloud=True)
        with self.tts.capture(sink):
            self.tts.speak("Привет")
        self.assertEqual(sink.said, ["Привет"])
        self.assertEqual(self.system.said, ["Привет"])

    def test_after_the_block_the_speakers_work_again(self):
        with self.tts.capture(Sink()):
            pass
        self.tts.speak("Слышно")
        self.assertEqual(self.system.said, ["Слышно"])

    def test_phrases_of_other_threads_are_not_taken(self):
        sink = Sink()
        with self.tts.capture(sink):
            thread = threading.Thread(target=lambda: self.tts.speak("Таймер сработал"))
            thread.start()
            thread.join(5)
            self.tts.speak("Для чата")
        self.assertEqual(sink.said, ["Для чата"])
        self.assertEqual(self.system.said, ["Таймер сработал"])

    def test_blocks_inside_blocks(self):
        outer, inner = Sink(), Sink()
        with self.tts.capture(outer):
            self.tts.speak("один")
            with self.tts.capture(inner):
                self.tts.speak("два")
            self.tts.speak("три")
        self.assertEqual((outer.said, inner.said), (["один", "три"], ["два"]))

    def test_a_chat_that_fails_does_not_break_the_command(self):
        class Broken(Sink):
            def say(self, text):
                raise OSError("no network")

        with self.assertLogs(level="ERROR"):
            with self.tts.capture(Broken()):
                self.tts.speak("Привет")
            self.assertEqual(self.system.said, [])
            with self.tts.capture(Broken(aloud=True)):
                self.tts.speak("Всё равно слышно")
        self.assertEqual(self.system.said, ["Всё равно слышно"])

    def test_the_block_is_left_when_the_command_fails(self):
        with self.assertRaises(RuntimeError):
            with self.tts.capture(Sink()):
                raise RuntimeError("boom")
        self.tts.speak("Слышно")
        self.assertEqual(self.system.said, ["Слышно"])


class Kaldi:
    """Vosk as a fake: the first second of the recording ends a phrase, the rest is in the final result."""
    created, fed = [], []

    def __init__(self, model, rate):
        Kaldi.created.append((model, rate))
        self.calls = 0

    def AcceptWaveform(self, chunk):
        Kaldi.fed.append(len(chunk))
        self.calls += 1
        return self.calls == 1

    def Result(self):
        return json.dumps({"text": "Поставь"})

    def FinalResult(self):
        return json.dumps({"text": "таймер на пять минут"})


class RecognizerTests(unittest.TestCase):
    def recognizer(self):
        recognizer = object.__new__(sm.SpeechRecognizer)
        recognizer._remote = threading.local()
        recognizer.command_model = "the model of commands"
        return recognizer

    def setUp(self):
        Kaldi.created.clear()
        Kaldi.fed.clear()

    def test_a_recording_is_recognized_with_the_model_of_commands(self):
        recognizer = self.recognizer()
        with mock.patch.object(sm.vosk, "KaldiRecognizer", Kaldi):
            text, stats = recognizer.transcribe(bytes(16000 * 2 * 5 // 2), 16000)
        self.assertEqual(text, "поставь таймер на пять минут")
        self.assertEqual(Kaldi.created, [("the model of commands", 16000)])
        self.assertEqual(Kaldi.fed, [32000, 32000, 16000], "the recording goes in pieces of one second")
        self.assertAlmostEqual(stats["audio_s"], 2.5)
        self.assertGreaterEqual(stats["decode_ms"], 0)

    def test_silence_gives_an_empty_text(self):
        class Silent(Kaldi):
            def Result(self):
                return json.dumps({"text": ""})

            def FinalResult(self):
                return json.dumps({"partial": ""})

        recognizer = self.recognizer()
        with mock.patch.object(sm.vosk, "KaldiRecognizer", Silent):
            self.assertEqual(recognizer.transcribe(bytes(32000), 16000)[0], "")
            self.assertEqual(recognizer.transcribe(b"", 16000)[0], "")

    def test_the_microphone_is_not_used_while_a_chat_answers(self):
        recognizer = self.recognizer()
        with recognizer.command_from(lambda: "москва"):
            self.assertEqual(recognizer.listen_command(), "москва")
        with recognizer.command_from(lambda: None):
            self.assertIsNone(recognizer.listen_command())

    def test_the_source_belongs_to_the_thread_that_set_it(self):
        recognizer = self.recognizer()
        seen = []
        with recognizer.command_from(lambda: "из чата"):
            thread = threading.Thread(target=lambda: seen.append(getattr(recognizer._remote, "source", None)))
            thread.start()
            thread.join(5)
        self.assertEqual(seen, [None], "the microphone of the assistant keeps working for the other threads")
        self.assertIsNone(getattr(recognizer._remote, "source", None))

    def test_sources_inside_sources(self):
        recognizer = self.recognizer()
        with recognizer.command_from(lambda: "внешний"):
            with recognizer.command_from(lambda: "внутренний"):
                self.assertEqual(recognizer.listen_command(), "внутренний")
            self.assertEqual(recognizer.listen_command(), "внешний")


class CommandMatchTests(unittest.TestCase):
    COMMANDS = [{"make": "shutdown", "triggers": ["выключи компьютер", "выключение компьютера"]},
                {"make": "restart", "triggers": ["перезагрузи компьютер"]},
                {"make": "get_current_time", "triggers": ["сколько времени"]}]

    def match(self, query):
        assistant = make_assistant()
        assistant.commands = self.COMMANDS
        with mock.patch.dict(Assistant.match_command.__globals__, {"fuzz": real_fuzz}):
            return assistant.match_command(query)

    def test_the_closest_phrase_wins(self):
        self.assertEqual(self.match("выключи компьютер")["make"], "shutdown")
        self.assertEqual(self.match("перезагрузи компьютер")["make"], "restart")
        self.assertEqual(self.match("сколько времени")["make"], "get_current_time")

    def test_a_small_slip_of_the_recognition_still_matches(self):
        self.assertEqual(self.match("выключи комп")["make"], "shutdown")

    def test_a_phrase_that_is_not_a_command(self):
        self.assertIsNone(self.match("расскажи анекдот про кота"))
        self.assertIsNone(self.match("включи"))

    def test_the_process_of_a_command_uses_the_same_lookup(self):
        # what the chat is warned about is what really runs
        source = Assistant.process_command.__code__.co_names
        self.assertIn("match_command", source)


class FakeWindow(FakeUI):
    def __init__(self):
        super().__init__()
        self.fired = []

    def notify(self, item, message):
        self.fired.append((item, message))


class RemoteCommandTests(unittest.TestCase):
    """handle_remote with the real pieces that make the chat work: the capture of the speech and the source of answers."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.tts, self.system = make_tts(self.dir.name)
        recognizer = object.__new__(sm.SpeechRecognizer)
        recognizer._remote = threading.local()
        recognizer.paused = False
        self.tasks = queue.Queue()
        recognizer.run_exclusive = lambda func: self.tasks.put(func) or "future"
        self.assistant = make_assistant()
        self.assistant.speech_recognizer = recognizer
        self.assistant.interface_manager = FakeWindow()
        self.assistant.tts = self.tts
        self.shown = []
        self.tts.on_speak = self.shown.append

    def run_task(self):
        self.tasks.get_nowait()()

    @property
    def window(self):
        return self.assistant.interface_manager

    def test_the_answer_goes_to_the_chat_and_the_window_shows_the_dialog(self):
        self.assistant.process_command = lambda query: self.tts.speak("Поставила таймер на пять минут.")
        sink = Sink()
        self.assertEqual(self.assistant.handle_remote("Поставь таймер на пять минут!", sink, "голосовое"), "future")
        self.assertEqual(self.window.messages,
                         [("user", "Поставь таймер на пять минут!", "Telegram · Анна · голосовое")])
        self.assertEqual(self.window.states, ["processing"])
        self.run_task()
        self.assertEqual(sink.said, ["Поставила таймер на пять минут."])
        self.assertEqual(self.system.said, [], "nothing is said aloud in an empty flat")
        self.assertEqual(self.shown, ["Поставила таймер на пять минут."])
        self.assertEqual(self.window.states[-1], "idle")

    def test_the_command_gets_the_phrase_in_the_form_of_a_spoken_one(self):
        seen = []
        self.assistant.process_command = seen.append
        self.assistant.handle_remote("Напомни в 18:00: позвонить!", Sink())
        self.run_task()
        self.assertEqual(seen, ["напомни в 18:00 позвонить"])

    def test_the_computer_says_it_aloud_when_the_owner_wants_so(self):
        self.assistant.process_command = lambda query: self.tts.speak("Привет")
        sink = Sink(aloud=True)
        self.assistant.handle_remote("привет", sink)
        self.run_task()
        self.assertEqual((sink.said, self.system.said), (["Привет"], ["Привет"]))

    def test_a_question_of_a_command_is_asked_in_the_chat(self):
        got = []

        def command(query):
            self.tts.speak("Для какого города?")
            got.append(self.assistant.speech_recognizer.listen_command())

        self.assistant.process_command = command
        sink = Sink(answers=["Москва!"])
        self.assistant.handle_remote("какая погода", sink)
        self.run_task()
        self.assertEqual(sink.said, ["Для какого города?"])
        self.assertEqual(sink.asked, 1)
        self.assertEqual(got, ["москва"], "the answer comes as a spoken phrase")
        self.assertEqual(self.window.messages[-1], ("user", "Москва!", "Telegram · Анна"))
        self.assertIn("listening", self.window.states)
        self.assertEqual(self.window.states[-1], "idle")

    def test_no_answer_to_the_question(self):
        got = []

        def command(query):
            self.tts.speak("Что написать?")
            got.append(self.assistant.speech_recognizer.listen_command())

        self.assistant.process_command = command
        self.assistant.handle_remote("напиши текст", Sink())
        self.run_task()
        self.assertEqual(got, [None])

    def test_an_answer_that_is_only_signs_is_no_answer(self):
        got = []
        self.assistant.process_command = lambda query: got.append(self.assistant.speech_recognizer.listen_command())
        self.assistant.handle_remote("какая погода", Sink(answers=["?!"]))
        self.run_task()
        self.assertEqual(got, [None])

    def test_a_failing_command_is_told_to_the_chat_and_the_state_is_restored(self):
        def command(query):
            raise RuntimeError("boom")

        self.assistant.process_command = command
        sink = Sink()
        with self.assertLogs(level="ERROR"):
            self.assistant.handle_remote("сделай что-нибудь", sink)
            self.run_task()
        self.assertEqual(len(sink.said), 1)
        self.assertIn("Не получилось", sink.said[0])
        self.assertEqual(self.window.states[-1], "idle")

    def test_the_microphone_off_is_remembered_in_the_state(self):
        self.assistant.speech_recognizer.paused = True
        self.assistant.process_command = lambda query: None
        self.assistant.handle_remote("привет", Sink())
        self.run_task()
        self.assertEqual(self.window.states[-1], "paused")

    def test_an_empty_phrase_starts_nothing(self):
        self.assertIsNone(self.assistant.handle_remote("  ?! ", Sink()))
        self.assertTrue(self.tasks.empty())
        self.assertEqual(self.window.messages, [])

    def test_after_the_command_the_assistant_speaks_and_listens_the_usual_way(self):
        self.assistant.process_command = lambda query: self.tts.speak("Из чата")
        self.assistant.handle_remote("привет", Sink())
        self.run_task()
        self.tts.speak("Для всех")
        self.assertEqual(self.system.said, ["Для всех"])
        self.assertIsNone(getattr(self.assistant.speech_recognizer._remote, "source", None))


class PlannerToChatTests(unittest.TestCase):
    def assistant(self, notify=True, bot="present"):
        assistant = make_assistant()
        assistant.interface_manager = FakeWindow()
        self.sent = []
        if bot is not None:
            assistant.telegram = types.SimpleNamespace(broadcast=self.sent.append)
        assistant.telegram_settings = types.SimpleNamespace(notify_planner=notify)
        return assistant

    TIMER = {"kind": "timer", "text": "", "length": 300, "due": "2026-10-28T08:00:00"}

    def test_a_timer_that_came_due_is_sent_to_the_chat(self):
        assistant = self.assistant()
        assistant.on_planner_fire(self.TIMER)
        self.assertEqual(self.sent, ["⏰ Таймер на 5 минут сработал."])
        self.assertEqual(len(assistant.interface_manager.fired), 1, "the window and the speakers are told as before")
        self.assertEqual(assistant.speech_recognizer.tasks.qsize(), 1)

    def test_the_owner_can_switch_it_off(self):
        assistant = self.assistant(notify=False)
        assistant.on_planner_fire(self.TIMER)
        self.assertEqual(self.sent, [])
        self.assertEqual(len(assistant.interface_manager.fired), 1)

    def test_without_a_bot_nothing_changes(self):
        assistant = self.assistant(bot=None)
        assistant.on_planner_fire(self.TIMER)
        self.assertEqual(len(assistant.interface_manager.fired), 1)

    def test_a_bot_that_fails_does_not_stop_the_alarm(self):
        assistant = self.assistant()

        def broken(text):
            raise OSError("no network")

        assistant.telegram = types.SimpleNamespace(broadcast=broken)
        with self.assertLogs(level="ERROR"):
            assistant.on_planner_fire(self.TIMER)
        self.assertEqual(assistant.speech_recognizer.tasks.qsize(), 1, "the alarm is still said on the computer")


class StartTests(unittest.TestCase):
    def assistant(self, ready):
        assistant = make_assistant()
        assistant.interface_manager = types.SimpleNamespace(set_service=lambda *a: None, telegram_changed=lambda: None,
                                                            notice=lambda text: None)
        self.started = []
        assistant.telegram = types.SimpleNamespace(state="off", start=lambda: self.started.append(1))
        assistant.telegram_settings = types.SimpleNamespace(ready=ready)
        return assistant

    def test_the_bot_starts_when_the_owner_has_switched_it_on(self):
        self.assistant(True).connect_telegram()
        self.assertEqual(self.started, [1])

    def test_the_bot_stays_off_otherwise(self):
        assistant = self.assistant(False)
        assistant.connect_telegram()
        self.assertEqual(self.started, [])
        self.assertTrue(callable(assistant.telegram.on_status))


if __name__ == "__main__":
    unittest.main()
