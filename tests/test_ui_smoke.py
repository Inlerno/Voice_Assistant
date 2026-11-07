"""The window as a whole: it is built, every page opens and every setting of the look is applied without errors.

Needs a screen (on Linux a virtual one is enough: xvfb-run python -m unittest); without one the tests are skipped.
"""
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import tkinter
import traceback
import types
import unittest
from concurrent.futures import Future

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

try:
    import sounddevice  # noqa: F401
except Exception:                                           # PortAudio is not there (a build server)
    stub = types.ModuleType("sounddevice")
    stub.default = types.SimpleNamespace(device=(1, 1))
    devices = [{"name": "Динамики", "max_input_channels": 0}, {"name": "Микрофон", "max_input_channels": 2}]
    stub.query_devices = lambda *args, **kwargs: devices if not args else devices[args[0]]
    sys.modules["sounddevice"] = stub


def screen_problem():
    """Why the window cannot be shown here (an empty string if it can)."""
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        return "нет экрана (запустите через xvfb-run)"
    try:
        import tkinter
        root = tkinter.Tk()
        root.destroy()
    except Exception as error:
        return f"Tk не запускается: {error}"
    return ""


PROBLEM = screen_problem()


class FakeRecognizer:
    """The recognizer as the settings page sees it: which engine works, and the loading of Whisper that takes a while."""

    def __init__(self):
        self.engine, self.whisper_state, self.calls = "vosk", "none", []
        self.engine_name = "Vosk"

    def use_engine(self, engine, folder=None, language="ru", threads=None, factory=None):
        self.calls.append((engine, folder.name if folder else None, language))
        if engine == "whisper" and folder is not None:
            self.whisper_state = "loading"
        else:
            self.engine, self.engine_name = "vosk", "Vosk"
            self.whisper_state = "failed" if engine == "whisper" else "none"

    def finish_loading(self, name):
        self.engine, self.engine_name, self.whisper_state = "whisper", f"Whisper {name}", "ready"


class FakeDownloads:
    """The download of a Whisper model: it waits at the gate (the test decides when it goes on) and watches the cancel."""

    def __init__(self, archive):
        self.archive = archive
        self.gate = threading.Event()
        self.gate.set()
        self.error = None
        self.requested = []

    def __call__(self, url, destination, progress=None, cancel=None):
        from src.speech.whisper_engine import SttCancelled, SttError
        self.requested.append(url)
        progress(0.25)
        while not self.gate.wait(0.02):
            if cancel is not None and cancel.is_set():
                raise SttCancelled("Загрузка отменена.")
        if cancel is not None and cancel.is_set():
            raise SttCancelled("Загрузка отменена.")
        if self.error:
            raise SttError(self.error)
        pathlib.Path(destination).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.archive, destination)
        progress(1.0)


def make_app(folder):
    from src.core.remote import show_bot_in_window
    from src.integrations.telegram.bot import TelegramBot
    from src.integrations.telegram.settings import TelegramSettings
    from src.interface.interface import VoiceAssistantApp
    from src.interface.preferences import Preferences
    from src.productivity.apps import AppLauncher
    from src.productivity.planner import Planner
    from src.productivity.skills import Productivity
    from src.speech import voices as V
    from src.speech.stt_control import SttControl
    from src.speech.stt_settings import SttSettings
    from src.speech.tts_module import TTS
    from src.speech.whisper_engine import WhisperLibrary
    from test_whisper_engine import make_archive

    folder = pathlib.Path(folder)
    planner = Planner(path=str(folder / "planner.json"))
    apps = AppLauncher(path=str(folder / "apps.json"), opener=lambda target: None, shortcut_dirs=[])
    productivity = Productivity(planner, apps)
    productivity.handle("поставь таймер на десять минут чай")
    productivity.handle("добавь задачу купить молоко")

    class Assistant:
        input_device = 1
        activation_words = ["астра"]
        listening = True
        appearance = "Dark"
        commands = [{"command": "browser", "triggers": ["открой браузер"], "say": "Открываю"}]

        class chat_gpt:
            messages = []
            model_name = "model"

        def set_input_device(self, index): pass
        def set_appearance(self, mode): self.appearance = mode
        def set_listening(self, value): self.listening = value
        def set_live_speech(self, value): self.live_speech = value
        def handle_text(self, text): return Future()
        def quick_add(self, text): return productivity.handle(text)

        def models_info(self):
            return {"small_model": "small", "big_model": "big", "big_model_state": "ready", "llm": "model",
                    "engine": self.speech_recognizer.engine_name}

    class SystemVoices:
        default_id = "sapi:one"
        def voices(self): return [V.Voice("sapi:one", "Голос Windows", V.SYSTEM, description="ru-RU")]
        def say(self, *args, **kwargs): pass
        def stop(self): pass

    class NoNeuralVoice:
        def available(self): return False
        def say(self, *args, **kwargs): pass
        def stop(self): pass

    assistant = Assistant()
    assistant.planner, assistant.apps = planner, apps
    # The engine of the recognition: the real rules of the choice, a recognizer and a download that are played
    assistant.speech_recognizer = FakeRecognizer()
    assistant.downloads = FakeDownloads(make_archive(folder / "model.tar.bz2", "base"))
    assistant.stt = SttControl(assistant.speech_recognizer, SttSettings(folder / "stt.json"),
                               WhisperLibrary(folder / "whisper", assistant.downloads))
    library = V.VoiceLibrary(root=str(folder / "voices"))
    assistant.tts = TTS(library=library, settings=V.TtsSettings(str(folder / "tts.json")),
                        system_engine_factory=SystemVoices, neural_engine=NoNeuralVoice())
    # The bot of Telegram: the page of the window shows and changes it (nothing is started until a token is given)
    assistant.telegram_settings = TelegramSettings(folder / "telegram.json")
    assistant.telegram = TelegramBot(assistant.telegram_settings, host=None, trust_env=False)
    app = VoiceAssistantApp(assistant, Preferences(folder / "ui" / "ui.json"))
    show_bot_in_window(assistant.telegram, app)
    return app


@unittest.skipIf(PROBLEM, PROBLEM)
class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.errors = []
        cls.app = make_app(cls.folder.name)
        cls.app.root.geometry("1200x760+0+0")
        cls.app.root.report_callback_exception = lambda *exc: cls.errors.append("".join(traceback.format_exception(*exc)))
        cls.pump(400)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.root.destroy()
        except Exception:
            pass
        cls.folder.cleanup()

    @classmethod
    def pump(cls, milliseconds):
        """Lets the window live for a while: the changes of the look are applied a moment after they are asked for."""
        end = time.monotonic() + milliseconds / 1000
        while time.monotonic() < end:
            cls.app.root.update()
            time.sleep(0.005)

    def change(self, **changes):
        changed = self.app.change(**changes)
        self.pump(250)
        return changed

    def pump_until(self, check, message, seconds=8.0):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.pump(40)
            if check():
                return
        self.fail(message)

    @staticmethod
    def texts_in(widget):
        """The captions of all labels inside a widget."""
        found = []
        for child in tkinter.Misc.winfo_children(widget):
            try:
                found.append(child.cget("text"))
            except Exception:
                pass
            found.extend(WindowTests.texts_in(child))
        return found

    def tearDown(self):
        self.assertEqual(self.errors, [], "в окне случились ошибки")

    def test_01_every_page_opens(self):
        from src.interface import theme as t
        for name, _title, _icon in t.PAGES:
            self.app.show_page(name)
            self.pump(60)
            self.assertEqual(self.app.current_page, name)
        self.app.show_page("home")

    def test_02_a_new_accent_builds_the_window_again_with_new_colors(self):
        from src.interface import theme as t
        before, accent = self.app.shell, t.ACCENT
        self.assertTrue(self.change(accent="ocean"))
        self.assertIsNot(self.app.shell, before)
        self.assertNotEqual(t.ACCENT, accent)
        self.assertTrue(self.change(accent="#3A7BD5"))
        self.assertEqual(self.app.prefs.accent.lower(), "#3a7bd5")

    def test_03_the_same_value_changes_nothing(self):
        shell = self.app.shell
        self.assertFalse(self.change(accent=self.app.prefs.accent))
        self.assertIs(self.app.shell, shell)

    def test_04_the_menu_goes_to_the_top_and_back(self):
        self.change(nav="top")
        self.assertIsNotNone(self.app.topbar)
        self.assertIsNone(self.app.sidebar)
        self.change(nav="left", nav_collapsed=True)
        self.assertIsNotNone(self.app.sidebar)
        self.change(nav_collapsed=False)

    def test_05_scale_shape_and_font(self):
        from src.interface import theme as t
        self.change(scale=1.25, radius=0.0, font="DejaVu Sans")
        self.assertLessEqual(self.app._built_scale, 1.25)
        self.assertEqual(t.FONT, "DejaVu Sans")
        self.assertEqual(t.r(12), 0)
        self.change(scale=1.0, radius=1.0, font="Segoe UI")
        self.assertEqual(self.app._built_scale, 1.0)

    def test_06_settings_that_need_no_rebuild_leave_the_window_alone(self):
        shell = self.app.shell
        self.change(user_name="Аня", opacity=0.95, show_timings=False, animations=False)
        self.assertIs(self.app.shell, shell)
        self.change(hero="logo")
        self.change(hero="minimal")
        self.change(background="flat")
        self.change(hero="orb", background="glow", user_name="", opacity=1.0, show_timings=True, animations=True)
        self.assertIs(self.app.shell, shell)

    def test_07_the_dialog_survives_a_rebuild(self):
        self.app.add_message("user", "поставь таймер на пять минут", "412 мс")
        self.app.add_message("assistant", "Поставила таймер на пять минут.")
        self.pump(150)
        before = len(self.app.history)
        self.change(accent="violet")
        self.assertEqual(len(self.app.history), before)
        self.assertGreaterEqual(before, 2)
        self.app.new_dialog()
        self.pump(100)
        self.change(accent="bordo")

    def test_07b_the_welcome_screen_comes_back_after_a_new_dialog(self):
        home = self.app.home
        self.app.add_message("user", "привет")
        self.pump(100)
        self.assertFalse(home.hero_mode)
        self.app.new_dialog()
        self.pump(200)
        self.assertTrue(home.hero_mode)
        self.assertTrue(home.canvas.winfo_ismapped())
        # The children go from the lowest window to the highest: the picture and the orb must not be hidden behind
        # the canvas that paints the frame of the page
        order = tkinter.Misc.winfo_children(home)      # (customtkinter's own version leaves the frame's canvas out)
        self.assertGreater(order.index(home.canvas), order.index(home._canvas))
        self.assertGreater(order.index(home.bar), order.index(home.canvas))

    def test_08_the_quick_search_opens_finds_and_closes(self):
        self.app.show_page("home")
        self.app.open_palette()
        self.pump(100)
        palette = self.app.palette
        self.assertTrue(palette.is_open)
        palette.field.insert(0, "тайм")
        palette.refresh()
        self.pump(50)
        self.assertEqual(palette.found[0].title, "Дела")
        self.assertTrue(palette.found[-1].title.startswith("Спросить Астру"))
        palette.run_selected()
        self.pump(150)
        self.assertEqual(self.app.current_page, "planner")
        self.assertFalse(palette.is_open)
        self.app.show_page("home")

    def test_09_light_and_dark(self):
        self.app.set_appearance("Light")
        self.pump(400)
        self.app.show_page("settings")
        self.pump(150)
        self.app.set_appearance("Dark")
        self.pump(400)
        self.app.show_page("home")

    def test_10_the_look_can_be_reset(self):
        self.change(accent="graphite", tint=0.1, depth=0.3, radius=1.5)
        self.app.reset_appearance()
        self.pump(300)
        from src.interface import preferences as P
        self.assertEqual(self.app.prefs.accent, P.DEFAULTS["accent"])
        self.assertEqual(self.app.prefs.radius, P.DEFAULTS["radius"])

    def test_11_settings_are_saved_to_the_disk(self):
        import json
        self.change(user_name="Проверка")
        saved = json.loads(pathlib.Path(self.app.prefs.path).read_text(encoding="utf-8"))
        self.assertEqual(saved["user_name"], "Проверка")

    def test_12_the_telegram_page_takes_a_token_links_an_account_and_unlinks_it(self):
        from unittest import mock
        from fake_telegram import TOKEN, FakeTelegram
        fake = FakeTelegram()
        self.addCleanup(fake.close)
        settings, bot = self.app.assistant.telegram_settings, self.app.assistant.telegram
        self.addCleanup(lambda: (bot.stop(), settings.update(token="", enabled=False)))
        settings.update(api_base=fake.url)
        self.app.show_page("telegram")
        self.pump(150)
        page = self.app.pages["telegram"]
        self.assertEqual(page.status.cget("text"), "Выключен")
        self.assertEqual(page.pair_button.cget("state"), "disabled")
        # A token that Telegram does not know is refused and not kept
        fake.fail("getMe", 401, "Unauthorized")
        page.token_entry.insert(0, TOKEN)
        page.save_token()
        self.pump_until(lambda: "не принимает" in page.token_note.cget("text"), "no word about the wrong token")
        self.assertEqual(settings.token, "")
        # The right one: the bot is switched on and connects
        page.token_entry.delete(0, "end")
        page.token_entry.insert(0, TOKEN)
        page.save_token()
        self.pump_until(lambda: bot.state == "ok", "the bot did not connect")
        self.assertEqual((settings.token, settings.enabled), (TOKEN, True))
        self.pump(150)
        self.assertIn("astra_test_bot", page.status.cget("text"))
        self.assertEqual(self.app.status.lights["tg"].state, "ok")
        self.assertEqual(page.token_entry.get(), "", "the token must not stay in the field")
        self.assertNotIn(TOKEN, " ".join(self.texts_in(page)))
        # Linking: a code appears, the person sends it, the account is in the list
        page.start_pairing()
        self.pump(100)
        self.assertTrue(page.pair_panel.winfo_ismapped())
        code = bot.pairing_info()[0]
        self.assertIn(code, page.pair_code.cget("text"))
        fake.text(42, f"/start {code}", first_name="Анна")
        self.pump_until(lambda: settings.is_allowed(42), "the account was not linked")
        self.pump_until(lambda: any("Анна" in text for text in self.texts_in(page.users_box)), "no account in the list")
        self.pump(100)
        self.assertFalse(page.pair_panel.winfo_ismapped(), "a code that was used must go away")
        self.assertTrue(any("привязан аккаунт Анна" in item[1] for item in self.app.history), "no notice in the dialog")
        # Unlinking asks first
        with mock.patch("src.interface.telegram_page.messagebox.askyesno", return_value=False):
            page.unlink(settings.users_snapshot()[0])
        self.assertTrue(settings.is_allowed(42))
        with mock.patch("src.interface.telegram_page.messagebox.askyesno", return_value=True):
            page.unlink(settings.users_snapshot()[0])
        self.assertFalse(settings.is_allowed(42))
        self.pump_until(lambda: any("никто не привязан" in text for text in self.texts_in(page.users_box)),
                        "the list was not emptied")
        # The switch stops the bot
        page._toggle(False)
        self.pump(150)
        self.assertEqual(bot.state, "off")
        self.assertEqual(self.app.status.lights["tg"].state, "off")
        self.assertFalse(settings.enabled)

    def test_13_options_of_the_telegram_page_are_saved(self):
        settings = self.app.assistant.telegram_settings
        self.addCleanup(lambda: settings.update(confirm_risky=True, notify_planner=True, speak_on_pc=False, proxy=""))
        self.app.show_page("telegram")
        self.pump(100)
        page = self.app.pages["telegram"]
        page.confirm_switch.toggle()
        page.remind_switch.toggle()
        page.aloud_switch.toggle()
        self.assertEqual((settings.confirm_risky, settings.notify_planner, settings.speak_on_pc),
                         (False, False, True))
        page.proxy_entry.delete(0, "end")
        page.proxy_entry.insert(0, "socks5h://127.0.0.1:1080")
        page.apply_connection()
        self.assertEqual(settings.proxy, "socks5h://127.0.0.1:1080")
        page.proxy_entry.delete(0, "end")
        page.proxy_entry.insert(0, "не адрес")
        page.apply_connection()
        self.assertEqual(settings.proxy, "socks5h://127.0.0.1:1080", "a wrong address must not be saved")
        self.assertIn("Прокси записывается так", page.connection_note.cget("text"))

    def test_14_a_notice_goes_to_the_dialog_and_the_light_of_the_bot_follows_its_state(self):
        self.app.notice("Telegram: привязан аккаунт Мария.")
        self.app.set_service("tg", "error")
        self.pump(150)
        self.assertEqual(self.app.history[-1][:2], ("system", "Telegram: привязан аккаунт Мария."))
        self.assertEqual(self.app.status.lights["tg"].state, "error")
        self.app.set_service("tg", "off")
        self.pump(60)
        self.change(accent="violet")
        self.assertEqual(self.app.history[-1][:2], ("system", "Telegram: привязан аккаунт Мария."))
        self.assertEqual(self.app.status.lights["tg"].state, "off")
        from src.interface import theme as t
        self.assertIn("telegram", [key for key, _, _ in t.PAGES])

    # ---------- The words that are being said ----------

    def draft_texts(self):
        """The captions of the dialog (the final messages, the notes and the draft)."""
        return self.texts_in(self.app.home.chat)

    def start_listening(self):
        self.app.show_page("home")
        self.app.new_dialog()
        self.app.set_state("listening")
        self.pump(120)

    def test_15_the_words_stand_in_the_dialog_while_they_are_said_and_the_final_text_takes_their_place(self):
        home = self.app.home
        self.start_listening()
        self.assertTrue(home.hero_mode)
        self.app.show_partial("поставь")
        self.pump(120)
        self.assertFalse(home.hero_mode, "the dialog opens for the first words")
        self.assertTrue(home.has_draft())
        self.assertIn("Поставь", self.draft_texts())
        self.assertIn("Вы · говорите…", self.draft_texts())
        # The bubble is written over, not added to
        rows = len(home.rows)
        self.app.show_partial("поставь таймер")
        self.pump(80)
        self.assertIn("Поставь таймер", self.draft_texts())
        self.assertNotIn("Поставь", self.draft_texts())
        self.assertEqual(len(home.rows), rows)
        # The recognizer ends the phrase: the message comes, then the live text is cleared
        self.app.add_message("user", "поставь таймер на пять минут", "распознавание 412 мс")
        self.app.show_partial("")
        self.pump(150)
        self.assertFalse(home.has_draft())
        self.assertEqual(self.app.draft, "")
        texts = self.draft_texts()
        self.assertIn("Поставь таймер на пять минут", texts)
        self.assertNotIn("Поставь таймер", texts)
        self.assertNotIn("Вы · говорите…", texts)
        self.app.new_dialog()
        self.pump(100)

    def test_16_the_draft_is_always_the_last_row_of_the_dialog(self):
        home = self.app.home
        self.start_listening()
        self.app.add_message("assistant", "Слушаю")
        self.app.show_partial("какая погода")
        self.pump(120)
        # Notes that come while somebody is speaking go above the words
        self.app.add_message("system", "Большая модель готова")
        self.app.add_message("system", "Слово «Астра» · голос совпал")
        self.pump(150)
        rows = home.chat.pack_slaves()
        draft = home._draft["row"]
        self.assertIs(rows[-1], draft)
        self.assertEqual(sum(1 for row in rows if row is draft), 1)
        self.assertEqual(len({id(row) for row in home.rows}), len(home.rows), "no row is listed twice")
        self.assertTrue(home.has_draft(), "the words of the person are still there")
        self.assertIn("Какая погода", self.draft_texts())
        # The assistant answers: the words that were not final are not going to be
        self.app.add_message("assistant", "Одну секунду")
        self.pump(150)
        self.assertFalse(home.has_draft())
        self.assertIn("Одну секунду", self.draft_texts())
        self.app.new_dialog()
        self.pump(100)

    def test_17_words_that_come_to_nothing_bring_the_welcome_screen_back(self):
        home = self.app.home
        self.start_listening()
        self.app.show_partial("и")
        self.pump(120)
        self.assertFalse(home.hero_mode)
        self.app.show_partial("")
        self.pump(150)
        self.assertFalse(home.has_draft())
        self.assertTrue(home.hero_mode, "an empty dialog must not stay behind a noise")
        self.assertTrue(home.canvas.winfo_ismapped())

    def test_18_words_that_come_to_nothing_leave_an_existing_dialog_alone(self):
        home = self.app.home
        self.app.new_dialog()
        self.app.add_message("user", "привет")
        self.app.set_state("listening")
        self.app.show_partial("и")
        self.pump(150)
        self.assertTrue(home.has_draft())
        self.app.show_partial("")
        self.pump(150)
        self.assertFalse(home.has_draft())
        self.assertFalse(home.hero_mode)
        self.assertIn("Привет", self.draft_texts())
        self.app.new_dialog()
        self.pump(100)

    def test_19_the_words_go_when_the_listening_is_over_for_any_reason(self):
        home = self.app.home
        self.start_listening()
        self.app.add_message("user", "привет")
        self.app.show_partial("пока")
        self.pump(120)
        self.assertTrue(home.has_draft())
        self.app.set_state("paused")
        self.pump(120)
        self.assertFalse(home.has_draft())
        self.assertEqual(self.app.draft, "")
        # Words that arrive late, when the microphone no longer listens, are not shown
        self.app.show_partial("опоздавшие слова")
        self.pump(120)
        self.assertFalse(home.has_draft())
        self.app.new_dialog()
        self.app.set_state("idle")
        self.pump(100)

    def test_20_the_switch_turns_the_live_text_off_and_tells_the_assistant(self):
        assistant = self.app.assistant
        self.start_listening()
        self.app.add_message("user", "привет")
        self.app.show_partial("пока")
        self.pump(120)
        self.assertTrue(self.app.home.has_draft())
        self.assertTrue(self.change(live_speech=False))
        self.assertFalse(assistant.live_speech)
        self.assertFalse(self.app.home.has_draft(), "the words that are on the screen go with the switch")
        self.app.show_partial("совсем новые слова")
        self.pump(120)
        self.assertFalse(self.app.home.has_draft())
        self.assertTrue(self.change(live_speech=True))
        self.assertTrue(assistant.live_speech)
        self.app.show_partial("снова вижу")
        self.pump(120)
        self.assertTrue(self.app.home.has_draft())
        self.app.new_dialog()
        self.app.set_state("idle")
        self.pump(100)

    def test_21_the_words_survive_a_rebuild_of_the_window(self):
        self.start_listening()
        self.app.add_message("user", "привет")
        self.app.show_partial("включи музыку")
        self.pump(150)
        self.change(accent="ocean")
        home = self.app.home
        self.assertTrue(home.has_draft())
        self.assertIn("Включи музыку", self.draft_texts())
        self.assertIs(home.chat.pack_slaves()[-1], home._draft["row"])
        self.app.add_message("user", "включи музыку")
        self.pump(150)
        self.assertFalse(self.app.home.has_draft())
        self.app.new_dialog()
        self.app.set_state("idle")
        self.pump(100)
        self.change(accent="violet")

    def test_22_the_switch_is_on_the_home_tab_of_the_settings(self):
        self.app.show_page("settings")
        page = self.app.pages["settings"]
        page.show_tab("home")
        self.pump(150)
        self.assertIn("Живой текст речи", self.texts_in(page.groups["home"]))
        self.app.show_page("home")


    # ---------- The engine of the recognition ----------

    def open_speech_tab(self):
        self.app.show_page("settings")
        self.app.pages["settings"].show_tab("speech")
        self.pump(150)
        return self.app.pages["settings"].speech

    def reset_stt(self):
        """Back to Vosk and no models, whatever the test before has done. A window that asks something would wait for
        a person for ever, so the questions of the page are caught: self.dialogs tells what was asked."""
        from unittest import mock
        from src.interface import stt_section
        self.dialogs = []
        for name in ("showwarning", "askyesno"):
            patcher = mock.patch.object(stt_section.messagebox, name, side_effect=(
                lambda *args, _name=name, **kwargs: self.dialogs.append((_name, args)) or False))
            patcher.start()
            self.addCleanup(patcher.stop)
        assistant = self.app.assistant
        for model_id in assistant.stt.library.installed():
            assistant.stt.library.remove(model_id)
        assistant.stt.settings.update(engine="vosk", whisper_model="", language="ru")
        assistant.speech_recognizer.use_engine("vosk")
        assistant.speech_recognizer.calls.clear()
        assistant.downloads.gate.set()
        assistant.downloads.error = None
        assistant.downloads.requested.clear()
        self.app.stt_downloads.clear()

    @staticmethod
    def buttons_in(widget, text):
        found = []
        for child in tkinter.Misc.winfo_children(widget):
            try:
                if child.cget("text") == text and hasattr(child, "invoke"):
                    found.append(child)
            except Exception:
                pass
            found.extend(WindowTests.buttons_in(child, text))
        return found

    def test_23_the_speech_tab_shows_the_engine_and_the_models_to_download(self):
        self.reset_stt()
        section = self.open_speech_tab()
        texts = self.texts_in(section)
        self.assertIn("Распознавание команд", texts)
        self.assertIn("Сейчас команды распознаёт Vosk.", texts)
        for title in ("Tiny", "Base", "Small", "Turbo", "Large v3"):
            self.assertIn(title, texts)
        self.assertEqual(len(self.buttons_in(section, "Скачать")), 5)
        self.assertEqual(section.engine_switch.value, "vosk")
        self.assertIn("Распознавание", self.texts_in(self.app.pages["settings"].tabs))

    def test_24_whisper_without_a_model_waits_for_one_and_vosk_keeps_working(self):
        self.reset_stt()
        section = self.open_speech_tab()
        section.choose_engine("whisper")
        self.pump(100)
        self.assertIn("Whisper ждёт модель.", self.texts_in(section))
        self.assertEqual(self.app.assistant.stt.settings.engine, "whisper")
        self.assertEqual(self.app.assistant.speech_recognizer.engine, "vosk")
        section.choose_engine("vosk")
        self.pump(100)
        self.assertIn("Сейчас команды распознаёт Vosk.", self.texts_in(section))

    def test_25_a_model_is_downloaded_with_a_progress_and_put_to_work_by_the_button(self):
        self.reset_stt()
        assistant = self.app.assistant
        assistant.downloads.gate.clear()
        section = self.open_speech_tab()
        self.buttons_in(section, "Скачать")[1].invoke()          # the second row: Base
        self.pump(150)
        self.assertEqual(list(self.app.stt_downloads), ["base"])
        self.assertEqual(len(self.buttons_in(section, "Отмена")), 1)
        self.assertEqual(len(self.buttons_in(section, "Скачать")), 4)
        self.pump_until(lambda: section.busy["base"].get() > 0.1, "the progress bar did not move")
        assistant.downloads.gate.set()
        self.pump_until(lambda: assistant.stt.library.is_installed("base"), "the model was not installed")
        self.pump(250)
        self.assertEqual(self.app.stt_downloads, {})
        self.assertEqual(len(self.buttons_in(section, "Использовать")), 1)
        self.buttons_in(section, "Использовать")[0].invoke()
        self.pump(150)
        settings = assistant.stt.settings
        self.assertEqual((settings.engine, settings.whisper_model), ("whisper", "base"))
        self.assertEqual(assistant.speech_recognizer.calls[-1], ("whisper", "base", "ru"))
        self.assertIn("Whisper Base загружается…", self.texts_in(section))
        assistant.speech_recognizer.finish_loading("base")
        section.refresh()
        self.assertIn("Сейчас команды распознаёт Whisper Base.", self.texts_in(section))
        self.assertIn("Используется", self.texts_in(section))
        self.assertEqual(self.dialogs, [])

    def test_26_a_model_that_arrives_while_whisper_waits_is_taken_at_once(self):
        self.reset_stt()
        assistant = self.app.assistant
        section = self.open_speech_tab()
        section.choose_engine("whisper")
        section.download("tiny")
        self.pump_until(lambda: assistant.stt.library.is_installed("tiny"), "the model was not installed")
        self.pump(300)
        self.assertEqual(assistant.stt.settings.whisper_model, "tiny")
        self.assertEqual(assistant.speech_recognizer.calls[-1], ("whisper", "tiny", "ru"))

    def test_27_a_download_can_be_cancelled_and_leaves_nothing(self):
        self.reset_stt()
        assistant = self.app.assistant
        assistant.downloads.gate.clear()
        section = self.open_speech_tab()
        section.download("small")
        self.pump(150)
        self.buttons_in(section, "Отмена")[0].invoke()
        self.pump_until(lambda: not self.app.stt_downloads, "the download did not stop")
        self.pump(250)
        self.assertEqual(assistant.stt.library.installed(), [])
        self.assertEqual(len(self.buttons_in(section, "Скачать")), 5)
        self.assertEqual(self.buttons_in(section, "Отмена"), [])
        self.assertEqual(self.dialogs, [], "a download that the person cancelled is not an error to tell about")

    def test_28_a_download_that_fails_tells_why(self):
        from unittest import mock
        self.reset_stt()
        assistant = self.app.assistant
        assistant.downloads.error = "Не удалось скачать модель. Проверьте интернет."
        section = self.open_speech_tab()
        with mock.patch("src.interface.stt_section.messagebox.showwarning") as warning:
            section.download("base")
            self.pump_until(lambda: warning.called, "the person was not told")
        self.assertIn("Проверьте интернет", warning.call_args[0][1])
        self.assertEqual(assistant.stt.library.installed(), [])
        self.assertEqual(len(self.buttons_in(section, "Скачать")), 5)

    def test_29_a_download_goes_on_when_the_window_is_built_again(self):
        self.reset_stt()
        assistant = self.app.assistant
        assistant.downloads.gate.clear()
        section = self.open_speech_tab()
        section.download("base")
        self.pump(150)
        self.change(accent="graphite")
        section = self.open_speech_tab()
        self.assertEqual(len(self.buttons_in(section, "Отмена")), 1, "the new page shows the download that goes on")
        assistant.downloads.gate.set()
        self.pump_until(lambda: assistant.stt.library.is_installed("base"), "the model was not installed")
        self.pump(250)
        self.assertEqual(len(self.buttons_in(self.open_speech_tab(), "Использовать")), 1)
        self.assertEqual(self.dialogs, [])
        self.change(accent="violet")

    def test_30_a_model_is_deleted_after_asking_and_vosk_takes_the_commands_back(self):
        from unittest import mock
        self.reset_stt()
        assistant = self.app.assistant
        assistant.stt.library.download("base")
        section = self.open_speech_tab()
        section.use("base")
        self.assertEqual(assistant.stt.settings.engine, "whisper")
        with mock.patch("src.interface.stt_section.messagebox.askyesno", return_value=False):
            section.remove("base")
        self.assertTrue(assistant.stt.library.is_installed("base"))
        with mock.patch("src.interface.stt_section.messagebox.askyesno", return_value=True):
            section.remove("base")
        self.assertFalse(assistant.stt.library.is_installed("base"))
        self.assertEqual((assistant.stt.settings.engine, assistant.speech_recognizer.engine), ("vosk", "vosk"))
        self.pump(100)
        self.assertIn("Сейчас команды распознаёт Vosk.", self.texts_in(section))

    def test_31_the_language_is_chosen_and_a_model_that_was_put_in_by_hand_is_listed(self):
        self.reset_stt()
        assistant = self.app.assistant
        mine = assistant.stt.library.folder("моя-модель")
        mine.mkdir(parents=True)
        for name in ("encoder.onnx", "decoder.onnx", "tokens.txt"):
            (mine / name).write_bytes(b"x")
        section = self.open_speech_tab()
        self.assertIn("моя-модель", self.texts_in(section))
        self.assertIn("Добавлена вручную", self.texts_in(section))
        section.choose_language("English")
        self.assertEqual(assistant.stt.settings.language, "en")
        section.choose_language("Определять по речи")
        self.assertEqual(assistant.stt.settings.language, "")

    def test_32_the_quick_search_finds_the_engine_and_the_model_card_names_it(self):
        self.reset_stt()
        self.app.open_palette()
        self.pump(100)
        titles = [entry.title for entry in self.app.palette_entries()]
        self.assertIn("Движок распознавания: Vosk или Whisper", titles)
        self.app.close_palette()
        entry = next(e for e in self.app.palette_entries() if e.title.startswith("Движок распознавания"))
        entry.action()
        self.pump(200)
        self.assertEqual((self.app.current_page, self.app.settings_tab), ("settings", "speech"))
        self.app.assistant.speech_recognizer.finish_loading("small")
        self.app.pages["settings"].show_tab("models")
        self.app.pages["settings"].models.refresh()
        self.assertTrue(any("Whisper small" in text for text in self.texts_in(self.app.pages["settings"].models)))
        self.reset_stt()
        self.app.pages["settings"].models.refresh()
        self.app.show_page("home")


if __name__ == "__main__":
    unittest.main()
