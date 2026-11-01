"""The window as a whole: it is built, every page opens and every setting of the look is applied without errors.

Needs a screen (on Linux a virtual one is enough: xvfb-run python -m unittest); without one the tests are skipped.
"""
import os
import pathlib
import sys
import tempfile
import time
import tkinter
import traceback
import types
import unittest
from concurrent.futures import Future

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

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


def make_app(folder):
    from src.interface.interface import VoiceAssistantApp
    from src.interface.preferences import Preferences
    from src.productivity.apps import AppLauncher
    from src.productivity.planner import Planner
    from src.productivity.skills import Productivity
    from src.speech import voices as V
    from src.speech.tts_module import TTS

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
        def handle_text(self, text): return Future()
        def quick_add(self, text): return productivity.handle(text)

        def models_info(self):
            return {"small_model": "small", "big_model": "big", "big_model_state": "ready", "llm": "model"}

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
    library = V.VoiceLibrary(root=str(folder / "voices"))
    assistant.tts = TTS(library=library, settings=V.TtsSettings(str(folder / "tts.json")),
                        system_engine_factory=SystemVoices, neural_engine=NoNeuralVoice())
    return VoiceAssistantApp(assistant, Preferences(folder / "ui" / "ui.json"))


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


if __name__ == "__main__":
    unittest.main()
