"""The window of the voice assistant: top navigation, pages and a status bar."""
import logging
import queue
from datetime import datetime
from pathlib import Path
from tkinter import PhotoImage

import customtkinter as ctk

from src.interface import graphics as g
from src.interface import theme as t
from src.interface.home_page import HomePage
from src.interface.pages import CommandsPage, LogPage, ModelPage, SettingsPage, confirm_risky
from src.interface.voice_page import VoicePage, device_by_index, short_device_name
from src.interface.widgets import StatusLight


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


# Graphical interface of the voice assistant
class VoiceAssistantApp:
    def __init__(self, assistant):
        self.assistant = assistant
        # Everything that comes from other threads (speech, logs) goes through this queue
        self.events = queue.Queue()
        self.state = "idle"
        self.pulse_phase = False
        self.current_page = "home"
        self.activation_word = self._get_activation_word()
        self.tabs = {}
        self.pages = {}

        ctk.set_appearance_mode(getattr(assistant, "appearance", "Dark"))
        ctk.set_default_color_theme("blue")

        self.root = ctk.CTk(fg_color=t.BG)
        self.root.title("Астра — голосовой помощник")
        self.root.geometry("1120x720")
        self.root.minsize(940, 600)
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(1, weight=1)

        self._build_navigation()
        self._build_pages()
        self._build_status_bar()
        self.show_page("home")
        self.apply_state("idle")

        # Connect logging system to the log page
        self.log_handler = TkinterLogHandler(self.events)
        logging.getLogger().addHandler(self.log_handler)
        logging.getLogger().setLevel(logging.INFO)

        self.root.after(50, self.poll_events)
        self.root.after(550, self.pulse)
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

    # ---------- Window construction ----------

    def _build_navigation(self):
        bar = ctk.CTkFrame(self.root, fg_color=t.BG, corner_radius=0, height=64)
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_columnconfigure(1, weight=1)
        bar.grid_propagate(False)
        ctk.CTkLabel(bar, text="Astra", font=(t.FONT, 18, "bold"), text_color=t.TEXT,
                     image=g.icon("sparkle", t.ACCENT, 20), compound="left").grid(
            row=0, column=0, padx=(26, 10), sticky="w"
        )
        tabs = ctk.CTkFrame(bar, fg_color="transparent")
        tabs.grid(row=0, column=1, sticky="w")
        for column, (key, title, icon_name) in enumerate(t.PAGES):
            cell = ctk.CTkFrame(tabs, fg_color="transparent")
            cell.grid(row=0, column=column, padx=2)
            button = ctk.CTkButton(
                cell, text=title, image=g.icon(icon_name, t.MUTED, 18), compound="left", height=38,
                corner_radius=19, font=(t.FONT, 13), fg_color="transparent", hover_color=t.HOVER,
                text_color=t.MUTED, width=40, command=lambda page=key: self.show_page(page),
            )
            button.pack(padx=2)
            underline = ctk.CTkFrame(cell, width=10, height=2, corner_radius=1, fg_color="transparent")
            underline.pack(fill="x", padx=16, pady=(1, 0))
            self.tabs[key] = (button, underline, icon_name)
        self.nav_mic = ctk.CTkLabel(bar, text="", font=(t.FONT, 12), text_color=t.MUTED)

    def _build_pages(self):
        stack = ctk.CTkFrame(self.root, fg_color=t.BG, corner_radius=0)
        stack.grid(row=1, column=0, sticky="nsew")
        stack.grid_columnconfigure(0, weight=1)
        stack.grid_rowconfigure(0, weight=1)
        self.home = HomePage(stack, self)
        self.commands_page = CommandsPage(stack, self)
        self.model_page = ModelPage(stack, self)
        self.voice_page = VoicePage(stack, self)
        self.log_page = LogPage(stack, self)
        self.settings_page = SettingsPage(stack, self)
        self.pages = {
            "home": self.home,
            "commands": self.commands_page,
            "model": self.model_page,
            "voice": self.voice_page,
            "log": self.log_page,
            "settings": self.settings_page,
        }
        for page in self.pages.values():
            page.grid(row=0, column=0, sticky="nsew")

    def _build_status_bar(self):
        bar = ctk.CTkFrame(self.root, fg_color=t.SURFACE, corner_radius=0, height=42, border_width=0)
        bar.grid(row=2, column=0, sticky="ew")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(1, weight=1)
        left = ctk.CTkFrame(bar, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w", padx=(22, 0))
        self.status_dot = ctk.CTkFrame(left, width=10, height=10, corner_radius=5, fg_color=t.OFF)
        self.status_dot.pack(side="left", pady=15)
        self.status_title = ctk.CTkLabel(left, text="", font=(t.FONT, 13, "bold"), text_color=t.TEXT)
        self.status_title.pack(side="left", padx=(8, 8))
        self.status_hint = ctk.CTkLabel(left, text="", font=(t.FONT, 12), text_color=t.MUTED)
        self.status_hint.pack(side="left")

        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.grid(row=0, column=2, sticky="e", padx=(0, 18))
        self.mic_label = ctk.CTkLabel(right, text="", font=(t.FONT, 12), text_color=t.MUTED, anchor="e")
        self.mic_label.pack(side="left", padx=(0, 14))
        self.lights = {}
        for key, caption in t.SERVICES:
            light = StatusLight(right, caption)
            light.pack(side="left", padx=3, pady=8)
            self.lights[key] = light
        self.update_mic_label(device_by_index(getattr(self.assistant, "input_device", None)))

    # ---------- Navigation ----------

    def show_page(self, name):
        if name not in self.pages:
            return
        self.current_page = name
        self.pages[name].tkraise()
        for key, (button, underline, icon_name) in self.tabs.items():
            active = key == name
            button.configure(
                text_color=t.TEXT if active else t.MUTED,
                image=g.icon(icon_name, t.TEXT if active else t.MUTED, 18),
            )
            underline.configure(fg_color=t.ACCENT if active else "transparent")

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

    def new_dialog(self):
        self.show_page("home")
        self.home.clear()
        history = getattr(getattr(self.assistant, "chat_gpt", None), "messages", None)
        if isinstance(history, list):
            history.clear()

    def open_microphone_check(self):
        self.show_page("voice")
        if not self.voice_page.mic_test_running:
            self.voice_page.toggle_mic_test()

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

    def on_appearance_changed(self):
        self.root.after(80, self.home.apply_theme)
        self.root.after(80, lambda: self.show_page(self.current_page))

    def update_mic_label(self, device_name):
        self.mic_label.configure(text=f"Микрофон: {short_device_name(device_name)}")

    # ---------- State ----------

    def apply_state(self, state):
        if state not in t.STATES:
            return
        self.state = state
        title, hint, color, _ = t.STATES[state]
        self.status_title.configure(text=title)
        self.status_hint.configure(text=hint.format(activation=self.activation_word))
        self.status_dot.configure(fg_color=color)
        self.pulse_phase = False
        self.sync_listening()

    # Soft blinking of the status dot while the assistant listens or speaks
    def pulse(self):
        if self.state in ("listening", "speaking"):
            self.pulse_phase = not self.pulse_phase
            color, dim = t.STATES[self.state][2:]
            self.status_dot.configure(fg_color=dim if self.pulse_phase else color)
        self.root.after(550, self.pulse)

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
            self.home.add_message(event[1], event[2], event[3])
        elif kind == "log":
            self.log_page.append(*event[1:])
        elif kind == "service":
            light = self.lights.get(event[1])
            if light is not None:
                light.set_state(event[2])
        elif kind == "models":
            self.model_page.refresh()
        elif kind in ("mic_started", "mic_level", "mic_result", "enroll_progress", "enroll_done"):
            self.voice_page.handle_event(kind, *event[1:])

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
