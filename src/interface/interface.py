"""The window of the voice assistant: the menu, the pages and the status bar. Its look follows the settings of the user.

A change of the colors, the font, the scale or the shape of the corners cannot be made widget by widget (every
widget has its colors from the moment it is made), so the whole window is built again from the new settings:
rebuild(). The dialog, the log, the state of the services and the open page are kept by the application itself and
put back, so the user only sees the new look appear. Settings that can be applied in place (the opacity of the
window, the name in the greeting, the background of the home page) never rebuild anything.
"""
import logging
import queue
import re
import sys
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import PhotoImage

import customtkinter as ctk

from src.interface import nav
from src.interface import palette
from src.interface import theme as t
from src.interface.apps_page import AppsPage
from src.interface.command_palette import CommandPalette, Entry
from src.interface.ctk_fixes import forget_destroyed_widgets
from src.interface.home_page import HomePage
from src.interface.notification import Notification
from src.interface.pages import CommandsPage, LogPage, confirm_risky
from src.interface.planner_page import PlannerPage
from src.interface.preferences import Preferences
from src.interface.settings_page import SettingsPage
from src.interface.telegram_page import TelegramPage
from src.interface.tts_page import TtsPage
from src.interface.voice_page import VoicePage, device_by_index, short_device_name
from src.interface.widgets import Toast, is_dark

logger = logging.getLogger(__name__)

START_SIZE = (1200, 760)
MIN_SIZE = (940, 600)
# What of the screen a window may take: the taskbar and the frame of the window need some room
SCREEN_SHARE = 0.96
TASKBAR = 90
HISTORY_LIMIT = 400
REBUILD_DELAY_MS = 40
SAVE_WINDOW_DELAY_MS = 700

# After a change of these settings the window is built again; the others are applied where they are
REBUILD_KEYS = frozenset({"accent", "tint", "depth", "font", "scale", "radius", "nav", "nav_collapsed"})
HOME_KEYS = frozenset({"hero", "background", "wallpaper_dim", "wallpaper_blur", "show_chips"})

# What to look for in the search for each page (besides its title)
PAGE_KEYWORDS = {
    "home": "чат диалог разговор вопрос",
    "planner": "таймер будильник напоминание задачи список дела",
    "apps": "программы запуск открыть ярлыки",
    "commands": "фразы что умеешь",
    "voice": "микрофон профиль голос владельца распознавание",
    "tts": "озвучка речь tts синтез голоса скорость громкость",
    "telegram": "телеграм telegram бот чат голосовые сообщения токен привязать аккаунт прокси",
    "log": "журнал логи ошибки",
    "settings": "оформление цвет тема шрифт размер масштаб обои",
}

# Keys of the shortcuts as Windows numbers them: they do not depend on the keyboard layout, so the shortcuts work with
# the Russian layout too (the letter that a layout types is not the same, the number of the key is)
WINDOWS_KEYS = {75: "k", 78: "n", 77: "m", 188: "comma", **{48 + digit: str(digit) for digit in range(1, 10)}}
GEOMETRY = re.compile(r"^(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)$")


def key_of(event):
    """The key of a shortcut: a letter, a digit or "comma"; empty if it is not one the program knows."""
    if sys.platform.startswith("win"):
        return WINDOWS_KEYS.get(event.keycode, "")
    return str(event.keysym).lower()


# Logging handler that passes records to the interface through a thread-safe queue
class TkinterLogHandler(logging.Handler):
    def __init__(self, events):
        super().__init__()
        self.events = events

    def emit(self, record):
        try:
            self.events.put((
                "log",
                record.levelno,
                datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
                record.getMessage(),
            ))
        except Exception:
            self.handleError(record)


def window_scaling(root):
    try:
        return root._get_window_scaling() or 1
    except Exception:
        return 1


def scale_limit(root):
    """The biggest scale of the interface at which its least window still fits the screen: a bigger one would push
    the window (and the menu in it) out of the screen, where nothing can be reached."""
    factor = window_scaling(root)
    width = root.winfo_screenwidth() / factor * SCREEN_SHARE
    height = (root.winfo_screenheight() - TASKBAR) / factor * SCREEN_SHARE
    return max(1.0, round(min(width / MIN_SIZE[0], height / MIN_SIZE[1]), 2))


# The window of 1200x760 does not fit a small laptop screen with the Windows scaling of 125-150%:
# the size is cut to the screen and the window is put in the middle
def fit_window(root, width=START_SIZE[0], height=START_SIZE[1], min_width=MIN_SIZE[0], min_height=MIN_SIZE[1]):
    scale = window_scaling(root)
    screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
    # What is really available in the units of the window (a part of the height is taken by the taskbar)
    usable_w, usable_h = screen_w / scale * SCREEN_SHARE, (screen_h - TASKBAR) / scale * SCREEN_SHARE
    width, height = min(width, int(usable_w)), min(height, int(usable_h))
    root.minsize(min(min_width, width), min(min_height, height))
    x = max(0, int((screen_w - width * scale) / 2))
    y = max(0, int((screen_h - height * scale) / 2) - 20)
    root.geometry(f"{width}x{height}+{x}+{y}")


def restore_window(root, saved, min_width=MIN_SIZE[0], min_height=MIN_SIZE[1]):
    """Puts the window where it was left: saved is [width, height, x, y] in pixels. The size is cut to the screen
    and the window is moved so that its title can be reached (a screen that was unplugged does not hide it)."""
    width, height, x, y = saved
    scale = window_scaling(root)
    screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
    width, height = min(width, screen_w), min(height, screen_h - 60)
    units_w, units_h = max(int(width / scale), 300), max(int(height / scale), 300)
    root.minsize(min(min_width, units_w), min(min_height, units_h))
    x = min(max(x, 0), max(0, screen_w - 160))
    y = min(max(y, 0), max(0, screen_h - 120))
    root.geometry(f"{units_w}x{units_h}+{x}+{y}")
    return True


# Graphical interface of the voice assistant
class VoiceAssistantApp:
    def __init__(self, assistant, preferences=None):
        self.assistant = assistant
        self.prefs = preferences or Preferences()
        # Everything that comes from other threads (speech, logs) goes through this queue
        self.events = queue.Queue()
        self.state = "idle"
        self.current_page = "home"
        self.settings_tab = "look"
        self.activation_word = self._get_activation_word()
        self.pages = {}
        self.notifications = []
        # What the window is built again from (see the docstring of the module)
        self.history = deque(maxlen=HISTORY_LIMIT)
        self.log_lines = deque(maxlen=t.LOG_LINES_LIMIT)
        self.service_states = {}
        self.tts_downloads = {}
        self.mic_device = device_by_index(getattr(assistant, "input_device", None))
        self.shell = self.sidebar = self.topbar = self.status = None
        self.palette = None
        self._rebuild_job = self._save_job = None
        self._built_scale = None
        self._built_dark = None

        t.apply(self.prefs)
        ctk.set_appearance_mode(getattr(assistant, "appearance", "Dark"))
        ctk.set_default_color_theme("blue")
        ctk.set_widget_scaling(self.prefs.scale)
        self._built_scale = self.prefs.scale

        self.root = ctk.CTk(fg_color=t.BG)
        self.root.title("Астра — голосовой помощник")
        limit = scale_limit(self.root)
        if self._built_scale > limit:
            # The scale was chosen on a bigger screen: it is cut for this one (the choice itself stays as it is)
            self._built_scale = limit
            ctk.set_widget_scaling(limit)
            self._release_window_limits()
        self._place_window()
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(0, weight=1)
        self.toast = Toast(self.root)
        self._install_bgerror()
        self._install_shortcuts()
        self._apply_window_prefs()
        ctk.AppearanceModeTracker.add(self._on_mode_changed)

        self.shell = self._build_shell()
        self.shell.grid(row=0, column=0, sticky="nsew")
        self._built_dark = is_dark()
        self.show_page("home")
        self.apply_state("idle")

        # Connect logging system to the log page
        self.log_handler = TkinterLogHandler(self.events)
        logging.getLogger().addHandler(self.log_handler)
        logging.getLogger().setLevel(logging.INFO)

        self.root.after(50, self.poll_events)
        self.root.after(300, self.set_window_icon)

    # ---------- Public thread-safe API ----------

    # Change the assistant state: idle, listening, processing, speaking, paused
    def set_state(self, state):
        self.events.put(("state", state))

    # Add a message to the dialog: role is "user", "assistant" or "system" (a small note without a bubble).
    # meta is a short remark shown next to the time, for example how many milliseconds the recognition took
    def add_message(self, role, text, meta=None):
        self.events.put(("message", role, text, meta))

    # State of a service shown in the status bar: name is ai, stt or tts; state is ok, busy, error or off
    def set_service(self, name, state):
        self.events.put(("service", name, state))

    # The models were changed (for example the big one has loaded): the model page reads them again
    def refresh_models(self):
        self.events.put(("models",))

    # Timers, alarms, reminders or tasks have changed (called from any thread)
    def planner_changed(self):
        self.events.put(("planner",))

    # Something came due: a window on top of everything (called from any thread)
    def notify(self, item, message):
        self.events.put(("notify", item, message))

    # The Telegram bot has a new state, a new account or a new code (called from any thread)
    def telegram_changed(self):
        self.events.put(("telegram",))

    # Something the owner should see at once, for example a new account that wrote to the bot (any thread)
    def notice(self, text):
        self.events.put(("notice", text))

    # ---------- Window construction ----------

    def scale_limit(self):
        return scale_limit(self.root)

    def wanted_scale(self):
        """The scale the window has to be built with: the chosen one, cut to what the screen allows."""
        return min(self.prefs.scale, scale_limit(self.root))

    def _release_window_limits(self):
        """customtkinter holds the window at its size for a second after a new scale is set; the window of the
        application is going to be moved and sized at once, so the hold is let go now."""
        try:
            self.root._set_scaled_min_max()
        except Exception:
            logger.debug("Не удалось снять ограничения окна", exc_info=True)

    def _place_window(self):
        scale = self._built_scale
        min_width, min_height = int(MIN_SIZE[0] * scale), int(MIN_SIZE[1] * scale)
        if self.prefs.remember_window and self.prefs.window:
            restore_window(self.root, self.prefs.window, min_width, min_height)
        else:
            fit_window(self.root, START_SIZE[0], START_SIZE[1], min_width, min_height)

    def _apply_window_prefs(self):
        self.root.configure(fg_color=t.BG)
        self.apply_opacity()
        try:
            self.root.attributes("-topmost", bool(self.prefs.always_on_top))
        except Exception:
            logger.debug("Не удалось закрепить окно поверх остальных", exc_info=True)

    def _build_shell(self):
        """Builds the menu, the pages and the status bar in a frame that is not shown yet."""
        left = self.prefs.nav == "left"
        shell = ctk.CTkFrame(self.root, fg_color=t.BG, corner_radius=0)
        shell.grid_columnconfigure(1, weight=1)
        shell.grid_rowconfigure(1, weight=1)
        if left:
            self.topbar = None
            self.sidebar = nav.Sidebar(shell, self, folded=self.prefs.nav_collapsed, state=self.state)
            self.sidebar.grid(row=0, column=0, rowspan=3, sticky="ns")
            column, span = 1, 1
        else:
            self.sidebar = None
            self.topbar = nav.TopNav(shell, self)
            self.topbar.grid(row=0, column=0, columnspan=2, sticky="ew")
            column, span = 0, 2
        stack = ctk.CTkFrame(shell, fg_color=t.BG, corner_radius=0)
        stack.grid(row=1, column=column, columnspan=span, sticky="nsew")
        stack.grid_columnconfigure(0, weight=1)
        stack.grid_rowconfigure(0, weight=1)
        self._build_pages(stack)
        self.status = nav.StatusBar(shell, self, show_state=not left, state=self.state)
        self.status.grid(row=2, column=column, columnspan=span, sticky="ew")
        for name, state in self.service_states.items():
            self.status.lights[name].set_state(state)
        self.status.mic.configure(text=f"Микрофон: {short_device_name(self.mic_device)}")
        return shell

    def _build_pages(self, stack):
        """Only the home page and the open one are made now: a page is made when it is opened for the first time
        (the whole set takes more than a second, and the window is built again after every change of the look)."""
        self.stack = stack
        self.pages = {}
        self.home = self.page("home")
        self.page(self.current_page)

    def page(self, name):
        """The page by its key; it is made now if it has not been made yet."""
        page = self.pages.get(name)
        if page is None and name in dict.fromkeys(key for key, _, _ in t.PAGES):
            page = self.pages[name] = self._make_page(name)
            page.grid(row=0, column=0, sticky="nsew")
        return page

    def _make_page(self, name):
        stack = self.stack
        if name == "home":
            return HomePage(stack, self, history=list(self.history))
        if name == "log":
            return LogPage(stack, self, lines=list(self.log_lines))
        if name == "tts":
            # A voice that is being downloaded keeps downloading when the window is built again
            return TtsPage(stack, self, cancel_events=self.tts_downloads)
        pages = {"planner": PlannerPage, "apps": AppsPage, "commands": CommandsPage, "voice": VoicePage,
                 "telegram": TelegramPage, "settings": SettingsPage}
        return pages[name](stack, self)

    # ---------- Changing the look ----------

    def change(self, **changes):
        """Changes settings of the look and applies them; returns the names of those that really changed."""
        changed = self.prefs.update(**changes)
        if not changed:
            return changed
        if "opacity" in changed:
            self.apply_opacity()
        if "always_on_top" in changed:
            self._apply_window_prefs()
        if "remember_window" in changed and self.prefs.remember_window:
            self._save_window()
        if changed & REBUILD_KEYS:
            self.schedule_rebuild()
            return changed
        if "animations" in changed:
            for part in (self.sidebar, self.status):
                if part is not None:
                    part.set_animated(self.prefs.animations)
        if changed & (HOME_KEYS | {"animations"}):
            self.home.apply_prefs()
        elif "user_name" in changed:
            self.home.canvas.refresh_greeting()
        return changed

    def reset_appearance(self):
        self.prefs.reset_appearance()
        self._apply_window_prefs()
        self.schedule_rebuild()

    def set_appearance(self, mode):
        """Dark, Light or System: the choice is kept by the assistant (it is not one of the settings of the look)."""
        if hasattr(self.assistant, "set_appearance"):
            self.assistant.set_appearance(mode)
        ctk.set_appearance_mode(mode)
        self.schedule_rebuild()

    def _on_mode_changed(self, mode):
        # customtkinter calls this when the theme changes, also when the system one does and the mode is "System"
        if self.shell is not None and (mode == "Dark") != self._built_dark:
            self.root.after(60, self.schedule_rebuild)

    def apply_opacity(self, value=None):
        try:
            self.root.attributes("-alpha", float(self.prefs.opacity if value is None else value))
        except Exception:
            logger.debug("Не удалось изменить прозрачность окна", exc_info=True)

    def preview_opacity(self, value):
        """While the slider moves the window is already seen with the new opacity (it is saved when it is let go)."""
        self.apply_opacity(value)

    def schedule_rebuild(self):
        if self._rebuild_job is None:
            self._rebuild_job = self.root.after(REBUILD_DELAY_MS, self.rebuild)

    def rebuild(self):
        """Builds the window again from the settings. The new one is built while the old one is still shown, so the
        window changes at once and never stays empty (only a new scale needs the old one to go first)."""
        self._rebuild_job = None
        self.close_palette(destroy=True)
        self.toast.hide()
        self._stop_activity()
        settings = self.pages.get("settings")
        scroll = settings.scroll_position() if settings is not None else 0.0
        try:
            self._rebuild_once()
        except Exception:
            # The look that was asked for cannot be built: the original one is known to work
            logger.exception("Не удалось перестроить окно, возвращаю исходное оформление")
            self.prefs.reset_appearance()
            self._rebuild_once()
        if scroll and self.current_page == "settings":
            self.root.after(120, lambda: self.pages["settings"].restore_scroll(scroll))

    def _rebuild_once(self):
        old = self.shell
        wanted = self.wanted_scale()
        scale_changed = self._built_scale != wanted
        if scale_changed and old is not None:
            # A new scale is cheaper to set while there is nothing in the window yet
            old.destroy()
            self.shell = old = None
        forget_destroyed_widgets()
        if scale_changed:
            # The window grows first: customtkinter fixes the size of the window to its current one while the new
            # scale is set and lets it go only a second later, so a window that grows after it would wait for that
            self._place_minimum(wanted)
            ctk.set_widget_scaling(wanted)
            self._built_scale = wanted
        t.apply(self.prefs)
        self._apply_window_prefs()
        shell = self._build_shell()
        shell.grid(row=0, column=0, sticky="nsew")
        self.root.update_idletasks()
        if old is not None:
            old.destroy()
        forget_destroyed_widgets()
        self.shell = shell
        self._built_dark = is_dark()
        self.show_page(self.current_page)
        self.apply_state(self.state)

    def _place_minimum(self, scale):
        """A bigger scale needs a bigger window: the least size grows with it and a window that is too small grows too."""
        factor = window_scaling(self.root)
        # Never more than the screen has: the least size is cut to it, so the window is never forced out of the screen
        screen_w = int(self.root.winfo_screenwidth() / factor * SCREEN_SHARE)
        screen_h = int((self.root.winfo_screenheight() - TASKBAR) / factor * SCREEN_SHARE)
        min_width, min_height = min(int(MIN_SIZE[0] * scale), screen_w), min(int(MIN_SIZE[1] * scale), screen_h)
        self.root.minsize(min_width, min_height)
        width = max(int(self.root.winfo_width() / factor), min_width)
        height = max(int(self.root.winfo_height() / factor), min_height)
        if width != int(self.root.winfo_width() / factor) or height != int(self.root.winfo_height() / factor):
            self.root.geometry(f"{width}x{height}")

    def _stop_activity(self):
        """What the voice page is doing with the microphone is asked to stop: its page is going to be made again."""
        voice = self.pages.get("voice")
        if voice is not None:
            voice.mic_test_stop.set()
            voice.enroll_stop.set()

    # ---------- Navigation ----------

    def show_page(self, name):
        page = self.page(name)
        if page is None:
            return
        self.current_page = name
        page.tkraise()
        for part in (self.sidebar, self.topbar):
            if part is not None:
                part.set_active(name)
        self.home.set_shown(name == "home")

    # ---------- Search and shortcuts ----------

    def open_palette(self):
        if self.palette is not None and not self.palette.winfo_exists():
            self.palette = None
        if self.palette is None:
            self.palette = CommandPalette(self.root, self)
        if self.palette.is_open:
            self.palette.close()
        else:
            self.palette.open(self.palette_entries())

    def close_palette(self, destroy=False):
        if self.palette is None:
            return
        self.palette.close()
        if destroy:
            self.palette.destroy()
            self.palette = None

    def palette_entries(self):
        entries = []
        for key, title, icon in t.PAGES:
            entries.append(Entry(title, f"Страница · {nav.shortcut_of(key)}", icon,
                                 lambda page=key: self.show_page(page), PAGE_KEYWORDS.get(key, "")))
        entries += [
            Entry("Новый диалог", "Действие · Ctrl+N", "plus", self.new_dialog, "очистить чат начать заново"),
            Entry("Включить или выключить микрофон", "Действие · Ctrl+M", "mic", self.toggle_listening,
                  "слушать пауза микрофон"),
            Entry("Проверить микрофон", "Действие", "mic", self.open_microphone_check, "уровень громкость тест"),
            Entry("Тёмная тема", "Оформление", "sliders", lambda: self.set_appearance("Dark"), "ночная dark"),
            Entry("Светлая тема", "Оформление", "sliders", lambda: self.set_appearance("Light"), "дневная light"),
            Entry("Тема как в Windows", "Оформление", "sliders", lambda: self.set_appearance("System"), "системная"),
            Entry("Меню сверху" if self.prefs.nav == "left" else "Меню слева", "Оформление", "sidebar",
                  lambda: self.change(nav="top" if self.prefs.nav == "left" else "left"), "навигация вкладки панель"),
        ]
        if self.prefs.nav == "left":
            entries.append(Entry("Развернуть меню" if self.prefs.nav_collapsed else "Свернуть меню", "Оформление",
                                 "sidebar", self.toggle_sidebar, "боковое панель"))
        for key, title, _, _ in palette.PRESETS:
            entries.append(Entry(f"Цвет: {title}", "Оформление", "sliders", lambda value=key: self.change(accent=value),
                                 "акцент цвет тема"))
        entries.append(Entry("Сбросить оформление", "Оформление", "sliders", self.reset_appearance,
                             "по умолчанию исходное"))
        for command in getattr(self.assistant, "commands", []):
            triggers = command.get("triggers") or []
            phrase = triggers[0].strip() if triggers else ""
            if phrase:
                entries.append(Entry(phrase.capitalize(), "Команда", "terminal",
                                     lambda item=command: self.run_command(item), " ".join(triggers[1:3])))
        return entries

    def _install_shortcuts(self):
        self.root.bind("<Control-KeyPress>", self.on_shortcut, add="+")
        self.root.bind("<Escape>", lambda _e: self.close_palette(), add="+")
        self.root.bind("<Button-1>", self._on_click, add="+")
        self.root.bind("<Configure>", self._on_configure, add="+")
        # A text field would take Ctrl+K itself (it deletes the rest of the line), so this one is handled before it
        self.root.bind_class("Entry", "<Control-Key-k>", self.on_shortcut)

    def on_shortcut(self, event):
        key = key_of(event)
        if key == "k":
            self.open_palette()
        elif key == "n":
            self.new_dialog()
        elif key == "m":
            self.toggle_listening()
        elif key == "comma":
            self.show_page("settings")
        elif key.isdigit() and 1 <= int(key) <= len(t.PAGES):
            self.show_page(t.PAGES[int(key) - 1][0])
        else:
            return None
        return "break"

    def _on_click(self, event):
        if self.palette is not None and self.palette.is_open and not self.palette.contains(event.widget):
            self.palette.close()

    def _install_bgerror(self):
        """A widget that is gone leaves timers behind it: Tk would report each of them as an error. They are harmless."""

        def handler(message=""):
            text = str(message)
            if "invalid command name" in text:
                logger.debug("Tk: %s", text.splitlines()[0] if text else text)
            else:
                logger.warning("Tk: %s", text)

        try:
            self.root.tk.createcommand("bgerror", handler)
        except Exception:
            logger.debug("Не удалось установить обработчик ошибок Tk", exc_info=True)

    # ---------- The place of the window ----------

    def _on_configure(self, event):
        if event.widget is not self.root or not self.prefs.remember_window:
            return
        if self._save_job is not None:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(SAVE_WINDOW_DELAY_MS, self._save_window)

    def _save_window(self):
        self._save_job = None
        try:
            if self.root.state() != "normal":
                return
            self.prefs.update(window=[self.root.winfo_width(), self.root.winfo_height(), self.root.winfo_x(),
                                      self.root.winfo_y()])
        except Exception:
            logger.exception("Не удалось запомнить размер окна")

    # ---------- Actions of the interface ----------

    # A command typed in the field: it runs like a spoken one
    def submit_text(self, text):
        self.show_page("home")
        self.assistant.handle_text(text)

    def toggle_listening(self):
        self.assistant.set_listening(not self.assistant.listening)
        self.sync_listening()

    def sync_listening(self):
        self.home.set_listening(self.assistant.listening)

    def toggle_sidebar(self):
        self.change(nav_collapsed=not self.prefs.nav_collapsed)

    def state_tooltip(self):
        title = t.STATES[self.state][0]
        action = "выключить" if self.assistant.listening else "включить"
        return f"{title}. Нажмите, чтобы {action} микрофон"

    def new_dialog(self):
        self.show_page("home")
        self.home.clear()
        self.history.clear()
        history = getattr(getattr(self.assistant, "chat_gpt", None), "messages", None)
        if isinstance(history, list):
            history.clear()

    def open_microphone_check(self):
        self.show_page("voice")
        voice = self.pages["voice"]
        if not voice.mic_test_running:
            voice.toggle_mic_test()

    # The first phrase of every command, for the "/" menu of the input field
    def command_phrases(self):
        phrases = []
        for command in getattr(self.assistant, "commands", []):
            triggers = command.get("triggers") or []
            phrase = triggers[0].strip() if triggers else ""
            if phrase and phrase not in phrases:
                phrases.append(phrase)
        return phrases

    # The "Выполнить" button of the commands page
    def run_command(self, command):
        triggers = command.get("triggers") or []
        if not triggers or not confirm_risky(self.root, command):
            return
        self.submit_text(triggers[0].strip())

    def show_notification(self, item, message):
        window = Notification(self, item, message, self.close_notification)
        self.notifications.append(window)
        window.place_at(len(self.notifications) - 1)
        for index, other in enumerate(self.notifications):
            other.place_at(index)

    def close_notification(self, window):
        if window in self.notifications:
            self.notifications.remove(window)
        for index, other in enumerate(self.notifications):
            other.place_at(index)

    def open_application(self, entry):
        self.page("apps").open_app(entry)

    def update_mic_label(self, device_name):
        self.mic_device = device_name
        self.status.mic.configure(text=f"Микрофон: {short_device_name(device_name)}")

    # ---------- State ----------

    def apply_state(self, state):
        if state not in t.STATES:
            return
        self.state = state
        title, hint, _, _ = t.STATES[state]
        hint = hint.format(activation=self.activation_word)
        if self.sidebar is not None:
            self.sidebar.set_state(state, title, hint)
        self.status.set_state(state, title, hint)
        self.home.set_state(state)
        self.sync_listening()

    # ---------- Events from other threads ----------

    def poll_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                try:
                    self.handle_event(event)
                except Exception:
                    logging.getLogger(__name__).exception("Ошибка при обновлении интерфейса: %s", event[0])
        except queue.Empty:
            pass
        self.root.after(50, self.poll_events)

    def handle_event(self, event):
        kind = event[0]
        if kind == "state":
            self.apply_state(event[1])
        elif kind == "message":
            stamp = datetime.now()
            self.history.append((event[1], event[2], event[3], stamp))
            self.home.add_message(event[1], event[2], event[3], stamp)
        elif kind == "log":
            self.log_lines.append(tuple(event[1:]))
            if "log" in self.pages:
                self.pages["log"].append(*event[1:])
        elif kind == "service":
            self.service_states[event[1]] = event[2]
            light = self.status.lights.get(event[1])
            if light is not None:
                light.set_state(event[2])
        elif kind == "models":
            if "settings" in self.pages:
                self.pages["settings"].models.refresh()
        elif kind == "planner":
            if "planner" in self.pages:
                self.pages["planner"].refresh()
        elif kind == "notify":
            self.show_notification(event[1], event[2])
        elif kind == "telegram":
            if "telegram" in self.pages:
                self.pages["telegram"].refresh()
        elif kind == "telegram_checked":
            # The answer to a check of a token started on the page: if the window was built again meanwhile, the
            # page is made again and takes the answer, so the person does not have to start over
            self.page("telegram").handle_event(kind, *event[1:])
        elif kind == "notice":
            self.history.append(("system", event[1], None, datetime.now()))
            self.home.add_message("system", event[1], None, self.history[-1][3])
            self.toast.show(event[1], "info", 5000)
        elif kind.startswith("tts_"):
            self._handle_tts_event(kind, event[1:])
        elif kind in ("mic_started", "mic_level", "mic_result", "enroll_progress", "enroll_done"):
            if "voice" in self.pages:
                self.pages["voice"].handle_event(kind, *event[1:])

    def _handle_tts_event(self, kind, args):
        page = self.pages.get("tts")
        if page is not None:
            page.handle_event(kind, *args)
        elif kind == "tts_downloaded":
            # The page is not there (the window was built again meanwhile): the result is still told to the user
            voice_id, error = args
            self.tts_downloads.pop(voice_id, None)
            self.toast.show(error or "Голос скачан", "error" if error else "ok", 4000)
        elif kind == "tts_imported":
            name, _, error = args
            self.toast.show(error or f"Голос «{name}» добавлен", "error" if error else "ok", 4000)

    # ---------- Misc ----------

    def _get_activation_word(self):
        words = getattr(self.assistant, "activation_words", None)
        return words[0].capitalize() if words else "Астра"

    def set_window_icon(self):
        icon_path = Path(__file__).resolve().parents[1] / "assets" / "img" / "icons" / "142x142.png"
        try:
            self._icon = PhotoImage(file=str(icon_path))
            self.root.iconphoto(True, self._icon)
        except Exception:
            pass

    def start_interface(self):
        self.root.mainloop()
