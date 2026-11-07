"""The Telegram bot against a fake Bot API server: linking an account, text and voice orders, answers, questions,
buttons for dangerous commands, limits and failures. Nothing here talks to the real Telegram."""
import io
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fake_telegram import TOKEN, FakeTelegram  # noqa: E402

from src.core.remote import Verdict, normalize_query  # noqa: E402
from src.integrations.telegram import bot as bot_module  # noqa: E402
from src.integrations.telegram.bot import TelegramBot  # noqa: E402
from src.integrations.telegram.settings import TelegramSettings  # noqa: E402

OWNER = 42
STRANGER = 777


def opus(seconds=1.0):
    """A real voice message: OGG with Opus inside (made with libsndfile)."""
    import numpy as np
    import soundfile
    t = np.arange(int(48000 * seconds)) / 48000
    data = (0.3 * np.sin(2 * np.pi * 440 * t)).astype("float32")
    buffer = io.BytesIO()
    soundfile.write(buffer, data, 48000, format="OGG", subtype="OPUS")
    return buffer.getvalue()


class FakeHost:
    """The assistant as the bot sees it. What a command does is a function of the test."""

    def __init__(self):
        self.behaviour = lambda text, io: io.say(f"Выполнено: {text}")
        self.recognized = "поставь таймер на пять минут"
        self.risky = {}
        self.local_only = set()
        self.checked, self.ran, self.transcribed = [], [], []
        self.engine = None             # what the assistant says recognized the voice (None for the usual Vosk)
        self.lock = threading.Lock()

    def check(self, text):
        with self.lock:
            self.checked.append(text)
        query = normalize_query(text)
        return Verdict(query, self.risky.get(query), query in self.local_only)

    def run(self, text, io, note=None):
        with self.lock:
            self.ran.append((text, note))
        future = Future()

        def job():
            try:
                future.set_result(self.behaviour(text, io))
            except BaseException as error:
                future.set_exception(error)

        threading.Thread(target=job, daemon=True, name="fake-assistant").start()
        return future

    def transcribe(self, pcm, rate):
        with self.lock:
            self.transcribed.append((len(pcm), rate))
        text = self.recognized
        return text, {"decode_ms": 12.0, "audio_s": len(pcm) / 2 / rate, "engine": self.engine}


class BotCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fake = FakeTelegram()
        self.addCleanup(self.fake.close)
        self.settings = TelegramSettings(Path(self.temp.name) / "telegram.json")
        self.settings.update(token=TOKEN, api_base=self.fake.url, enabled=True)
        self.host = FakeHost()
        self.states, self.notices, self.changes = [], [], []
        self.bot = TelegramBot(self.settings, self.host, on_status=lambda s, t: self.states.append((s, t)),
                               on_change=lambda: self.changes.append(1), on_notice=self.notices.append,
                               trust_env=False)
        self.addCleanup(self.bot.stop)
        # Waits are shortened so that the tests do not sleep
        for name, value in (("BACKOFF", (0.05, 0.05, 0.05)), ("CONFLICT_WAIT", 0.05), ("POLL_TIMEOUT", 1)):
            patcher = mock.patch.object(bot_module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def link(self, user_id=OWNER):
        self.settings.link(user_id, "Анна", "anna")

    def start(self):
        self.assertTrue(self.bot.start())
        self.wait(lambda: self.bot.state == "ok", "the bot did not connect")

    def wait(self, check, message="timeout", timeout=8.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if check():
                return
            time.sleep(0.02)
        self.fail(message)

    def say(self, text, user=OWNER):
        self.fake.text(user, text)

    def answer_to(self, text, user=OWNER, expect=None):
        """Writes the text and waits for the reply that contains `expect` (by default: any new message)."""
        before = len(self.fake.sent)
        self.say(text, user)
        if expect is None:
            self.assertTrue(self.fake.wait_for(lambda: len(self.fake.sent) > before), f"no answer to {text!r}")
            return self.fake.sent[before]["text"]
        message = self.fake.wait_text(expect, user)
        self.assertIsNotNone(message, f"no answer with {expect!r}; sent: {self.fake.texts()}")
        return message["text"]


class ConnectionTests(BotCase):
    def test_the_bot_connects_and_prepares_the_chat(self):
        self.start()
        self.assertEqual(self.bot.username, "astra_test_bot")
        self.assertGreaterEqual(self.fake.count("deleteWebhook"), 1)
        self.assertEqual([c["command"] for c in self.fake.commands], ["help", "cancel", "unlink"])
        self.assertEqual(self.states[0][0], "connecting")
        self.assertEqual(self.states[-1][0], "ok")
        self.assertIn("astra_test_bot", self.states[-1][1])

    def test_without_a_token_it_does_not_start(self):
        self.settings.update(token="")
        self.assertFalse(self.bot.start())
        self.assertEqual(self.bot.state, "off")
        self.assertIn("токен", self.bot.status_text.lower())

    def test_switched_off_it_does_not_start(self):
        self.settings.update(enabled=False)
        self.assertFalse(self.bot.start())
        self.assertEqual(self.bot.state, "off")

    def test_a_wrong_token_is_reported_and_reading_stops(self):
        self.fake.fail("getMe", 401, "Unauthorized")
        self.assertTrue(self.bot.start())
        self.wait(lambda: self.bot.state == "error")
        self.assertIn("токен", self.bot.status_text.lower())
        time.sleep(0.3)
        self.assertEqual(self.fake.count("getUpdates"), 0)

    def test_a_conflict_is_reported_and_the_bot_tries_again(self):
        self.fake.fail("getUpdates", 409, "Conflict: terminated by other getUpdates request", times=2)
        self.assertTrue(self.bot.start())
        self.wait(lambda: any(s == "error" for s, _ in self.states))
        self.wait(lambda: self.bot.state == "ok", "the bot did not recover after the conflict")
        self.assertTrue(any("другая программа" in text for s, text in self.states if s == "error"))

    def test_the_bot_recovers_after_a_failure_of_the_network_side(self):
        self.fake.fail("getUpdates", 502, "Bad Gateway", times=2)
        self.assertTrue(self.bot.start())
        self.wait(lambda: any(s == "error" for s, _ in self.states))
        self.wait(lambda: self.bot.state == "ok")
        self.link()
        self.assertIn("Выполнено", self.answer_to("привет", expect="Выполнено"))

    def test_stop_ends_the_reading(self):
        self.start()
        self.bot.stop()
        self.assertEqual(self.bot.state, "off")
        self.assertFalse(self.bot.running)
        count = self.fake.count("getUpdates")
        time.sleep(1.5)
        self.assertLessEqual(self.fake.count("getUpdates") - count, 1)

    def test_start_again_replaces_the_reader(self):
        self.start()
        self.bot.restart()
        self.wait(lambda: self.bot.state == "ok")
        self.link()
        self.assertIn("Выполнено", self.answer_to("привет", expect="Выполнено"))
        # one message, one answer: the old reader is gone
        time.sleep(0.5)
        self.assertEqual(sum(1 for t in self.fake.texts() if "Выполнено" in t), 1)


class LinkingTests(BotCase):
    def test_the_code_links_the_account(self):
        self.start()
        code, link, left = self.bot.new_pairing()
        self.assertRegex(code, r"^[A-Z2-9]{4}-[A-Z2-9]{4}$")
        self.assertEqual(link, f"https://t.me/astra_test_bot?start={code.replace('-', '')}")
        self.assertGreater(left, 500)
        reply = self.answer_to(f"/start {code}", expect="привязан")
        self.assertIn("Готово", reply)
        self.assertTrue(self.settings.is_allowed(OWNER))
        self.assertEqual(self.settings.users_snapshot()[0]["username"], "anna")
        self.assertTrue(self.changes)
        self.assertTrue(any("привязан аккаунт" in n for n in self.notices))
        # a code works once
        self.assertIsNone(self.bot.pairing_info())

    def test_link_command_and_a_typed_code_with_dash_and_lower_case(self):
        self.start()
        code, _, _ = self.bot.new_pairing()
        self.answer_to(f"/link {code.lower()}", expect="Готово")
        self.assertTrue(self.settings.is_allowed(OWNER))

    def test_a_wrong_code_does_not_link(self):
        self.start()
        self.bot.new_pairing()
        self.assertIn("не подходит", self.answer_to("/start AAAA-AAAA"))
        self.assertFalse(self.settings.is_allowed(OWNER))

    def test_without_a_valid_code_a_guess_is_refused(self):
        self.start()
        self.assertIn("не подходит", self.answer_to("/start 12345678"))
        self.assertFalse(self.settings.is_allowed(OWNER))

    def test_too_many_wrong_codes_lock_the_person_out_even_with_the_right_code(self):
        self.start()
        code, _, _ = self.bot.new_pairing()
        for _ in range(5):
            self.answer_to("/start AAAA-AAAA", expect="не подходит")
            self.fake.sent.clear()
        self.assertIn("Слишком много", self.answer_to(f"/start {code}"))
        self.assertFalse(self.settings.is_allowed(OWNER))

    def test_a_stranger_gets_one_hint_and_nothing_is_run(self):
        self.start()
        first = self.answer_to("выключи компьютер", user=STRANGER)
        self.assertIn("/link", first)
        before = len(self.fake.sent)
        self.say("поставь таймер", STRANGER)
        self.say("привет", STRANGER)
        time.sleep(0.5)
        self.assertEqual(len(self.fake.sent), before)
        self.assertEqual(self.host.ran, [])
        self.assertEqual(self.host.checked, [])
        self.assertTrue(any("не привязанный" in n for n in self.notices))

    def test_group_chats_and_bots_are_ignored(self):
        self.start()
        self.link()
        self.fake.text(OWNER, "привет", chat_type="group", chat_id=-100500)
        self.fake.text(OWNER, "привет", is_bot=True)
        self.fake.text(STRANGER, "/start AAAA", chat_type="supergroup", chat_id=-1)
        time.sleep(0.6)
        self.assertEqual(self.fake.sent, [])
        self.assertEqual(self.host.ran, [])

    def test_a_linked_account_that_writes_start_again_is_told_so(self):
        self.start()
        self.link()
        self.assertIn("уже привязан", self.answer_to("/start"))

    def test_unlink_takes_the_rights_away(self):
        self.start()
        self.link()
        self.assertIn("отвязан", self.answer_to("/unlink"))
        self.assertFalse(self.settings.is_allowed(OWNER))
        self.assertIn("/link", self.answer_to("поставь таймер"))
        self.assertEqual(self.host.ran, [])

    def test_unlinking_in_the_program_stops_the_orders_at_once(self):
        self.start()
        self.link()
        self.assertIn("Выполнено", self.answer_to("привет", expect="Выполнено"))
        self.assertTrue(self.bot.unlink(OWNER))
        self.assertIn("/link", self.answer_to("ещё раз"))


class TextOrderTests(BotCase):
    def setUp(self):
        super().setUp()
        self.link()
        self.start()

    def test_a_text_order_is_run_and_the_answer_comes_back(self):
        self.assertEqual(self.answer_to("Поставь таймер на 5 минут!"), "Выполнено: Поставь таймер на 5 минут!")
        self.assertEqual(self.host.ran, [("Поставь таймер на 5 минут!", None)])

    def test_phrases_of_one_command_come_as_one_message(self):
        self.host.behaviour = lambda text, io: [io.say("Первое."), io.say("Второе."), io.say("Третье.")]
        self.assertEqual(self.answer_to("сделай три"), "Первое.\nВторое.\nТретье.")
        time.sleep(0.3)
        self.assertEqual(len(self.fake.sent), 1)

    def test_a_command_that_says_nothing_is_done(self):
        self.host.behaviour = lambda text, io: None
        self.assertEqual(self.answer_to("тихая команда"), "Готово.")

    def test_markdown_of_the_model_is_cleaned(self):
        self.host.behaviour = lambda text, io: io.say("## Итог\n**Важно**: `код` и __ещё__")
        self.assertEqual(self.answer_to("что-нибудь"), "Итог\nВажно: код и ещё")

    def test_a_long_answer_goes_in_pieces(self):
        self.host.behaviour = lambda text, io: io.say(("Слово " * 1000).strip())
        self.assertTrue(self.fake.wait_for(lambda: True))
        self.say("длинный ответ")
        self.assertTrue(self.fake.wait_for(lambda: len(self.fake.sent) >= 2))
        self.assertTrue(all(len(m["text"]) <= 4096 for m in self.fake.sent))
        self.assertEqual(" ".join(self.fake.texts()).split(), ["Слово"] * 1000)

    def test_a_very_long_answer_is_flushed_before_the_command_ends(self):
        release = threading.Event()

        def behaviour(text, io):
            io.say("а" * 3600)
            release.wait(5)
            io.say("конец")

        self.host.behaviour = behaviour
        self.say("долгая команда")
        self.assertTrue(self.fake.wait_for(lambda: len(self.fake.sent) >= 1))
        self.assertEqual(len(self.fake.texts()[0]), 3600)
        release.set()
        self.assertIsNotNone(self.fake.wait_text("конец"))

    def test_a_failing_command_is_reported(self):
        def behaviour(text, io):
            raise RuntimeError("secret detail")

        self.host.behaviour = behaviour
        reply = self.answer_to("сломайся")
        self.assertIn("Не получилось", reply)
        self.assertNotIn("secret detail", reply)

    def test_a_sticker_gets_a_hint(self):
        before = len(self.fake.sent)
        self.fake.sticker(OWNER)
        self.assertTrue(self.fake.wait_for(lambda: len(self.fake.sent) > before))
        self.assertIn("текст и голосовые", self.fake.sent[-1]["text"])
        self.assertEqual(self.host.ran, [])

    def test_help_and_unknown_commands(self):
        self.assertIn("поставь таймер", self.answer_to("/help"))
        self.assertIn("нет такой команды", self.answer_to("/bogus"))
        self.assertIn("поставь таймер", self.answer_to("/help@astra_test_bot"))

    def test_an_order_that_is_only_signs_is_not_run(self):
        self.assertIn("Слушаю", self.answer_to("?!"))
        self.assertEqual(self.host.ran, [])

    def test_the_typing_sign_is_shown_while_the_command_works(self):
        release = threading.Event()
        self.host.behaviour = lambda text, io: release.wait(5)
        with mock.patch.object(bot_module, "TYPING_EVERY", 0.1):
            self.say("думай долго")
            self.assertTrue(self.fake.wait_for(lambda: self.fake.count("sendChatAction") >= 3))
        release.set()
        self.assertEqual(self.fake.wait_text("Готово")["text"], "Готово.")

    def test_commands_are_run_one_after_another(self):
        order = []
        gate = threading.Event()

        def behaviour(text, io):
            order.append(("start", text))
            if text == "первая":
                gate.wait(5)
            order.append(("end", text))
            io.say(text)

        self.host.behaviour = behaviour
        self.say("первая")
        self.wait(lambda: order)
        self.say("вторая")
        time.sleep(0.3)
        self.assertEqual(order, [("start", "первая")])
        gate.set()
        self.assertIsNotNone(self.fake.wait_text("вторая"))
        self.assertEqual(order, [("start", "первая"), ("end", "первая"), ("start", "вторая"), ("end", "вторая")])

    def test_too_many_waiting_commands_are_refused(self):
        gate = threading.Event()
        self.host.behaviour = lambda text, io: gate.wait(5)
        with mock.patch.object(bot_module, "MAX_QUEUED", 2):
            for index in range(5):
                self.say(f"команда {index}")
            self.assertIsNotNone(self.fake.wait_text("Слишком много команд"))
        gate.set()

    def test_the_rate_limit(self):
        with mock.patch.object(bot_module, "RATE_MESSAGES", 3):
            for index in range(6):
                self.say(f"привет {index}")
            self.assertIsNotNone(self.fake.wait_text("Слишком много сообщений"))
        time.sleep(0.3)
        self.assertEqual(sum(1 for t in self.fake.texts() if "Слишком много сообщений" in t), 1)

    def test_an_old_message_is_not_obeyed(self):
        self.fake.text(OWNER, "выключи свет", date=int(time.time()) - 3600)
        self.assertIsNotNone(self.fake.wait_text("Пропустила сообщений: 1"))
        self.assertEqual(self.host.ran, [])



class VoiceOrderTests(BotCase):
    def setUp(self):
        super().setUp()
        self.link()
        self.start()

    def voice(self, data, **options):
        before = len(self.fake.sent)
        self.fake.voice(OWNER, data, **options)
        self.assertTrue(self.fake.wait_for(lambda: len(self.fake.sent) > before), "no answer to the voice message")
        return self.fake.sent[before]["text"]

    def test_a_voice_message_is_recognized_and_run(self):
        self.assertEqual(self.voice(opus()), "🎙 «поставь таймер на пять минут»\nВыполнено: поставь таймер на пять минут")
        self.assertEqual(len(self.host.transcribed), 1)
        # what the assistant gets is 16 kHz sound: one second of it
        self.assertEqual(self.host.transcribed[0], (32000, 16000))
        text, note = self.host.ran[0]
        self.assertEqual(text, "поставь таймер на пять минут")
        self.assertIn("голосовое", note)
        self.assertIn("12 мс", note)

    def test_whisper_is_named_in_the_note_and_vosk_is_not(self):
        self.voice(opus())
        self.assertEqual(self.host.ran[0][1], "голосовое · распознавание 12 мс")
        self.host.engine = "Whisper base"
        self.voice(opus())
        self.assertEqual(self.host.ran[1][1], "голосовое · распознавание (Whisper base) 12 мс")

    def test_nothing_recognized(self):
        self.host.recognized = ""
        self.assertIn("Не расслышала", self.voice(opus()))
        self.assertEqual(self.host.ran, [])

    def test_a_file_that_is_not_sound(self):
        self.assertIn("повреждён", self.voice(b"this is not an audio file at all" * 20))
        self.assertEqual(self.host.ran, [])

    def test_a_message_that_is_too_long(self):
        self.assertIn("слишком длинное", self.voice(opus(), duration=600))
        self.assertEqual(self.host.transcribed, [])

    def test_a_file_that_is_too_big_for_a_bot(self):
        self.fake.voice(OWNER, opus(), duration=3)
        update = self.fake.updates[-1]
        update["message"]["voice"]["file_size"] = 30 * 1024 * 1024
        self.assertTrue(self.fake.wait_for(lambda: len(self.fake.sent) >= 1))
        self.assertIn("20 МБ", self.fake.sent[0]["text"])
        self.assertEqual(self.host.transcribed, [])

    def test_telegram_does_not_give_the_file(self):
        self.fake.voice(OWNER, opus())
        self.fake.files.clear()
        self.assertTrue(self.fake.wait_for(lambda: len(self.fake.sent) >= 1))
        self.assertIn("Не получилось забрать голосовое", self.fake.sent[0]["text"])
        self.assertEqual(self.host.ran, [])

    def test_the_recognizer_fails(self):
        def broken(pcm, rate):
            raise RuntimeError("model is gone")

        self.host.transcribe = broken
        self.assertIn("Не получилось распознать", self.voice(opus()))

    def test_a_dangerous_voice_command_asks_for_a_tap(self):
        self.host.recognized = "выключи компьютер"
        self.host.risky["выключи компьютер"] = "выключит компьютер"
        reply = self.voice(opus())
        self.assertIn("«выключи компьютер»", reply)
        self.assertIsNotNone(self.fake.wait_text("выключит компьютер"))
        self.assertEqual(self.host.ran, [])


class ConfirmationTests(BotCase):
    def setUp(self):
        super().setUp()
        self.link()
        self.host.risky["выключи компьютер"] = "выключит компьютер"
        self.host.local_only.add("нажми клавиши")
        self.start()

    def ask(self):
        self.say("Выключи компьютер")
        message = self.fake.wait_text("выключит компьютер")
        self.assertIsNotNone(message, self.fake.texts())
        buttons = message["reply_markup"]["inline_keyboard"][0]
        self.assertEqual([b["text"] for b in buttons], ["Да, выполнить", "Отмена"])
        return {b["text"]: b["callback_data"] for b in buttons}

    def test_nothing_runs_before_the_tap(self):
        self.ask()
        time.sleep(0.4)
        self.assertEqual(self.host.ran, [])

    def test_yes_runs_the_command(self):
        buttons = self.ask()
        self.fake.tap(OWNER, buttons["Да, выполнить"])
        self.assertIsNotNone(self.fake.wait_text("Выполнено: Выключи компьютер"))
        self.assertEqual(self.host.ran, [("Выключи компьютер", None)])
        self.assertEqual(self.fake.answers[-1]["text"], "Выполняю")
        self.assertIn("✅", self.fake.edits[-1]["text"])
        self.assertEqual(self.fake.edits[-1]["reply_markup"], {"inline_keyboard": []})

    def test_cancel_does_not_run_it(self):
        buttons = self.ask()
        self.fake.tap(OWNER, buttons["Отмена"])
        self.assertTrue(self.fake.wait_for(lambda: self.fake.edits))
        self.assertIn("Отменено", self.fake.edits[-1]["text"])
        time.sleep(0.3)
        self.assertEqual(self.host.ran, [])

    def test_a_button_works_once(self):
        buttons = self.ask()
        self.fake.tap(OWNER, buttons["Да, выполнить"])
        self.assertIsNotNone(self.fake.wait_text("Выполнено"))
        self.fake.tap(OWNER, buttons["Да, выполнить"])
        self.assertTrue(self.fake.wait_for(lambda: len(self.fake.answers) >= 2))
        self.assertIn("Время вышло", self.fake.answers[-1]["text"])
        self.assertEqual(len(self.host.ran), 1)

    def test_somebody_else_cannot_tap_the_button(self):
        buttons = self.ask()
        self.settings.link(STRANGER, "Чужой", "")
        self.fake.tap(STRANGER, buttons["Да, выполнить"])
        self.assertTrue(self.fake.wait_for(lambda: self.fake.answers))
        time.sleep(0.3)
        self.assertEqual(self.host.ran, [])
        # the owner can still confirm
        self.fake.tap(OWNER, buttons["Да, выполнить"])
        self.assertIsNotNone(self.fake.wait_text("Выполнено"))

    def test_a_button_of_a_stranger_is_ignored(self):
        buttons = self.ask()
        self.fake.tap(STRANGER, buttons["Да, выполнить"])
        self.assertTrue(self.fake.wait_for(lambda: self.fake.answers))
        self.assertEqual(self.host.ran, [])

    def test_an_old_button_does_not_work(self):
        buttons = self.ask()
        with mock.patch.object(bot_module, "CONFIRM_SECONDS", -1):
            self.fake.tap(OWNER, buttons["Да, выполнить"])
            self.assertTrue(self.fake.wait_for(lambda: self.fake.answers))
        self.assertIn("Время вышло", self.fake.answers[-1]["text"])
        self.assertEqual(self.host.ran, [])

    def test_a_made_up_button_does_nothing(self):
        for data in ("c:nonsense:1", "c:x", "zzz", ""):
            self.fake.tap(OWNER, data)
        self.assertTrue(self.fake.wait_for(lambda: len(self.fake.answers) >= 4))
        self.assertEqual(self.host.ran, [])

    def test_without_the_switch_the_command_runs_at_once(self):
        self.settings.update(confirm_risky=False)
        self.assertIn("Выполнено", self.answer_to("выключи компьютер", expect="Выполнено"))

    def test_a_command_for_the_computer_only_is_refused(self):
        self.assertIn("только на самом компьютере", self.answer_to("нажми клавиши"))
        self.assertEqual(self.host.ran, [])


class QuestionTests(BotCase):
    def setUp(self):
        super().setUp()
        self.link()
        self.start()
        self.results = []

        def weather(text, io):
            io.say("Укажите город:")
            city = io.ask()
            self.results.append(city)
            io.say(f"Погода в {city}: солнечно" if city else "Не удалось распознать город.")

        self.host.behaviour = weather

    def test_the_next_message_is_the_answer(self):
        self.say("какая погода")
        message = self.fake.wait_text("Укажите город")
        self.assertIn("Жду ответ", message["text"])
        self.say("Москва")
        self.assertIsNotNone(self.fake.wait_text("Погода в Москва: солнечно"))
        self.assertEqual(self.results, ["Москва"])
        # the answer was not taken for a new order
        self.assertEqual(len(self.host.ran), 1)

    def test_a_voice_message_can_answer(self):
        self.host.recognized = "санкт петербург"
        self.say("какая погода")
        self.assertIsNotNone(self.fake.wait_text("Укажите город"))
        self.fake.voice(OWNER, opus())
        self.assertIsNotNone(self.fake.wait_text("Погода в санкт петербург"))
        self.assertIsNotNone(self.fake.wait_text("«санкт петербург»"))

    def test_cancel_stops_the_question(self):
        self.say("какая погода")
        self.assertIsNotNone(self.fake.wait_text("Укажите город"))
        self.say("/cancel")
        self.assertIsNotNone(self.fake.wait_text("Не удалось распознать город"))
        self.assertEqual(self.results, [None])

    def test_cancel_without_a_question(self):
        self.assertIn("нечего отменять", self.answer_to("/cancel"))

    def test_no_answer_in_time(self):
        with mock.patch.object(bot_module, "ANSWER_TIMEOUT", 0.3):
            self.say("какая погода")
            self.assertIsNotNone(self.fake.wait_text("Не удалось распознать город"))
        self.assertEqual(self.results, [None])

    def test_a_message_after_the_question_is_a_new_order(self):
        with mock.patch.object(bot_module, "ANSWER_TIMEOUT", 0.2):
            self.say("какая погода")
            self.assertIsNotNone(self.fake.wait_text("Не удалось распознать город"))
        self.host.behaviour = lambda text, io: io.say("новая команда")
        self.say("привет")
        self.assertIsNotNone(self.fake.wait_text("новая команда"))


class DeliveryTests(BotCase):
    def setUp(self):
        super().setUp()
        self.link()
        self.start()

    def test_telegram_asks_to_wait_and_the_message_is_sent_later(self):
        self.fake.fail("sendMessage", 429, "Too Many Requests: retry after 1", retry_after=0.2)
        started = time.monotonic()
        self.assertIn("Выполнено", self.answer_to("привет", expect="Выполнено"))
        self.assertGreaterEqual(time.monotonic() - started, 0.15)

    def test_a_blip_of_the_network_does_not_lose_the_answer(self):
        self.fake.fail("sendMessage", 502, "Bad Gateway")
        # a 502 answer is an error of Telegram: it is not repeated, the next messages still go
        self.say("раз")
        self.assertTrue(self.fake.wait_for(lambda: self.fake.count("sendMessage") >= 1))
        self.assertIn("Выполнено: два", self.answer_to("два", expect="Выполнено: два"))

    def test_a_person_who_blocked_the_bot_does_not_break_it(self):
        self.fake.fail("sendMessage", 403, "Forbidden: bot was blocked by the user")
        self.say("раз")
        self.assertTrue(self.fake.wait_for(lambda: self.fake.count("sendMessage") >= 1))
        self.assertIn("Выполнено: два", self.answer_to("два", expect="Выполнено: два"))

    def test_the_token_is_not_in_the_log_when_the_network_fails(self):
        import logging
        self.bot.stop()
        self.settings.update(api_base="http://127.0.0.1:9")
        with self.assertLogs(level=logging.DEBUG) as logs:
            self.bot.start()
            self.wait(lambda: self.bot.state == "error")
            time.sleep(0.2)
            logging.getLogger("test").info("end of the log")
        text = "\n".join(logs.output) + self.bot.status_text
        self.assertNotIn(TOKEN, text)
        self.assertNotIn("AAEtest", text)
        self.assertIn("Нет соединения", self.bot.status_text)


class BroadcastTests(BotCase):
    def test_every_linked_account_gets_the_text(self):
        self.link(OWNER)
        self.link(43)
        self.start()
        self.assertEqual(self.bot.broadcast("⏰ Таймер на 5 минут сработал."), 2)
        for user in (OWNER, 43):
            self.assertIsNotNone(self.fake.wait_text("Таймер на 5 минут сработал", user), f"nothing for {user}")

    def test_a_stranger_gets_nothing(self):
        self.link(OWNER)
        self.start()
        self.bot.broadcast("⏰ Будильник!")
        self.assertIsNotNone(self.fake.wait_text("Будильник", OWNER))
        self.say("привет", STRANGER)
        self.assertIsNotNone(self.fake.wait_text("личный бот", STRANGER))
        self.assertEqual([t for t in self.fake.texts(STRANGER) if "Будильник" in t], [])

    def test_without_accounts_or_without_a_running_bot_nothing_is_sent(self):
        self.assertEqual(self.bot.broadcast("текст"), 0)
        self.link(OWNER)
        self.assertEqual(self.bot.broadcast("текст"), 0, "the bot is not running")
        self.bot.stop()
        self.settings.unlink(OWNER)
        self.start()
        self.assertEqual(self.bot.broadcast("текст"), 0, "nobody is linked")
        self.assertEqual(self.bot.broadcast("   "), 0)
        self.assertEqual(self.fake.count("sendMessage"), 0)

    def test_markdown_is_cleaned(self):
        self.link(OWNER)
        self.start()
        self.bot.broadcast("**Важно**: позвонить")
        self.assertIsNotNone(self.fake.wait_text("Важно: позвонить", OWNER))

    def test_a_person_who_blocked_the_bot_does_not_stop_the_others(self):
        self.link(OWNER)
        self.link(43)
        self.start()
        self.fake.fail("sendMessage", 403, "Forbidden: bot was blocked by the user")
        self.bot.broadcast("⏰ Напоминаю: позвонить маме.")
        self.assertIsNotNone(self.fake.wait_text("позвонить маме", 43))
        self.assertEqual(self.fake.texts(OWNER), [])

    def test_an_account_that_was_unlinked_before_the_sending_gets_nothing(self):
        self.link(OWNER)
        self.link(43)
        self.start()
        self.settings.unlink(43)
        with mock.patch.object(self.settings, "users_snapshot", return_value=[
                {"id": OWNER, "name": "", "username": "", "since": ""}, {"id": 43, "name": "", "username": "", "since": ""}]):
            self.bot.broadcast("⏰ Таймер")
        self.assertIsNotNone(self.fake.wait_text("Таймер", OWNER))
        time.sleep(0.3)
        self.assertEqual(self.fake.texts(43), [])


if __name__ == "__main__":
    unittest.main()
