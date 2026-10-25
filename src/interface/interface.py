import logging
import queue
import threading
from datetime import datetime
from pathlib import Path
from tkinter import PhotoImage

import customtkinter as ctk
import sounddevice as sd

from src.audio.mic_test import check_microphone

# Fonts (Segoe UI is the native Windows font, Tk falls back to another one elsewhere)
FONT = "Segoe UI"
FONT_MONO = "Consolas"

# Colors as (light theme, dark theme) pairs
BG = ("#F3F5F9", "#0E1117")
SIDEBAR = ("#FFFFFF", "#141922")
CARD = ("#FFFFFF", "#1A212D")
BORDER = ("#E2E6EE", "#262F3F")
TEXT = ("#1B2230", "#E8ECF4")
MUTED = ("#6B7690", "#8E9AB3")
ACCENT = ("#3D6BFF", "#5B86FF")
ACCENT_HOVER = ("#2F58DB", "#4A73E6")
NAV_ACTIVE = ("#E8EEFF", "#1F2A44")
NAV_HOVER = ("#EEF1F7", "#1B2231")
BUBBLE_USER = ACCENT
BUBBLE_BOT = ("#E9EDF5", "#212A39")

# Assistant states: title, hint, dot color, dimmed dot color (for the pulse animation)
STATES = {
    "idle": ("Ожидание", "Скажите «{activation}»", "#7C89A6", "#7C89A6"),
    "listening": ("Слушаю", "Говорите команду", "#2DD4A0", "#1E8F70"),
    "processing": ("Выполняю", "Обрабатываю запрос", "#F5B63D", "#F5B63D"),
    "speaking": ("Говорю", "Астра отвечает", "#8B7BFF", "#5E52BF"),
}

APPEARANCE_LABELS = {"Тёмная": "Dark", "Светлая": "Light", "Системная": "System"}
EXAMPLE_COMMANDS = (
    "открой браузер", "какая погода", "включи музыку",
    "сколько время", "найди в интернете", "какой сегодня день",
)
# Colors of the microphone check verdict as (light theme, dark theme) pairs
MIC_STATUS_COLORS = {
    "ok": ("#15803D", "#2DD4A0"),
    "quiet": ("#B45309", "#F5B63D"),
    "loud": ("#B45309", "#F5B63D"),
    "silent": ("#B91C1C", "#F87171"),
    "error": ("#B91C1C", "#F87171"),
    "cancelled": MUTED,
}
MIC_TEST_SECONDS = 5
LOG_LINES_LIMIT = 500
LOG_LEVEL_TAGS = {logging.WARNING: "warning", logging.ERROR: "error", logging.CRITICAL: "error"}


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
        self.wrap_length = 460
        self.message_labels = []
        self.empty_state = None
        self.nav_buttons = {}
        self.pages = {}
        # Microphone check: worker thread, its stop flag and the smoothed meter position
        self.mic_test_thread = None
        self.mic_test_stop = threading.Event()
        self.meter_value = 0.0
        self.activation_word = self._get_activation_word()

        ctk.set_appearance_mode(getattr(assistant, "appearance", "Dark"))
        ctk.set_default_color_theme("blue")

        self.root = ctk.CTk(fg_color=BG)
        self.root.title("Астра — голосовой помощник")
        self.root.geometry("980x660")
        self.root.minsize(780, 540)
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        self.create_sidebar()
        self.create_content()
        self.show_page("dialog")
        self.apply_state("idle")

        # Connect logging system to the log page
        self.log_handler = TkinterLogHandler(self.events)
        logging.getLogger().addHandler(self.log_handler)
        logging.getLogger().setLevel(logging.INFO)

        self.root.after(50, self.poll_events)
        self.root.after(550, self.pulse)
        self.root.after(300, self.set_window_icon)

    # ---------- Public thread-safe API ----------

    # Change the assistant state: idle, listening, processing, speaking
    def set_state(self, state):
        self.events.put(("state", state))

    # Add a message to the dialog: role is "user" or "assistant"
    def add_message(self, role, text):
        self.events.put(("message", role, text))

    # ---------- Window construction ----------

    def create_sidebar(self):
        sidebar = ctk.CTkFrame(self.root, width=240, corner_radius=0, fg_color=SIDEBAR)
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(5, weight=1)

        # Logo and name
        brand = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=20, pady=(24, 18))
        logo = ctk.CTkFrame(brand, width=42, height=42, corner_radius=12, fg_color=ACCENT)
        logo.pack(side="left")
        logo.pack_propagate(False)
        ctk.CTkLabel(logo, text="А", font=(FONT, 22, "bold"), text_color="#FFFFFF").pack(expand=True)
        names = ctk.CTkFrame(brand, fg_color="transparent")
        names.pack(side="left", padx=(12, 0))
        ctk.CTkLabel(names, text="Астра", font=(FONT, 20, "bold"), text_color=TEXT, anchor="w").pack(anchor="w")
        ctk.CTkLabel(names, text="голосовой помощник", font=(FONT, 12), text_color=MUTED, anchor="w").pack(anchor="w")

        # Status card
        status_card = ctk.CTkFrame(sidebar, corner_radius=14, fg_color=BG, border_width=1, border_color=BORDER)
        status_card.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 18))
        status_row = ctk.CTkFrame(status_card, fg_color="transparent")
        status_row.pack(fill="x", padx=14, pady=(12, 0))
        self.status_dot = ctk.CTkFrame(status_row, width=12, height=12, corner_radius=6, fg_color="#7C89A6")
        self.status_dot.pack(side="left")
        self.status_title = ctk.CTkLabel(status_row, text="", font=(FONT, 15, "bold"), text_color=TEXT)
        self.status_title.pack(side="left", padx=(10, 0))
        self.status_hint = ctk.CTkLabel(
            status_card, text="", font=(FONT, 12), text_color=MUTED, anchor="w", justify="left"
        )
        self.status_hint.pack(fill="x", padx=14, pady=(2, 12))

        # Navigation
        for row, (key, title) in enumerate((("dialog", "Диалог"), ("log", "Журнал"), ("settings", "Настройки")), start=2):
            button = ctk.CTkButton(
                sidebar,
                text=title,
                anchor="w",
                height=40,
                corner_radius=10,
                font=(FONT, 14),
                fg_color="transparent",
                text_color=TEXT,
                hover_color=NAV_HOVER,
                command=lambda page=key: self.show_page(page),
            )
            button.grid(row=row, column=0, sticky="ew", padx=16, pady=2)
            self.nav_buttons[key] = button

        # Current microphone
        mic_box = ctk.CTkFrame(sidebar, fg_color="transparent")
        mic_box.grid(row=6, column=0, sticky="sew", padx=20, pady=(0, 20))
        ctk.CTkLabel(mic_box, text="Микрофон", font=(FONT, 11), text_color=MUTED, anchor="w").pack(fill="x")
        self.mic_label = ctk.CTkLabel(
            mic_box, text="", font=(FONT, 12), text_color=TEXT, anchor="w", justify="left", wraplength=190
        )
        self.mic_label.pack(fill="x")
        self.update_mic_label(self.get_device_by_index(getattr(self.assistant, "input_device", None)))

    def create_content(self):
        content = ctk.CTkFrame(self.root, fg_color="transparent")
        content.grid(row=0, column=1, sticky="nsew", padx=(24, 24), pady=(22, 22))
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(1, weight=1)

        # Page header
        header = ctk.CTkFrame(content, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.grid_columnconfigure(0, weight=1)
        self.page_title = ctk.CTkLabel(header, text="", font=(FONT, 24, "bold"), text_color=TEXT, anchor="w")
        self.page_title.grid(row=0, column=0, sticky="w")
        self.page_subtitle = ctk.CTkLabel(header, text="", font=(FONT, 13), text_color=MUTED, anchor="w")
        self.page_subtitle.grid(row=1, column=0, sticky="w")
        self.clear_button = ctk.CTkButton(
            header,
            text="Очистить",
            width=100,
            height=34,
            corner_radius=10,
            font=(FONT, 13),
            fg_color="transparent",
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            hover_color=NAV_HOVER,
        )
        self.clear_button.grid(row=0, column=1, rowspan=2, sticky="e")

        body = ctk.CTkFrame(content, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        self.create_dialog_page(body)
        self.create_log_page(body)
        self.create_settings_page(body)

    def create_dialog_page(self, parent):
        page = ctk.CTkFrame(parent, corner_radius=16, fg_color=CARD, border_width=1, border_color=BORDER)
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)
        self.pages["dialog"] = page

        self.chat = ctk.CTkScrollableFrame(page, fg_color="transparent", corner_radius=16)
        self.chat.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        # Keep the message width proportional to the window width
        self.chat._parent_canvas.bind("<Configure>", self.on_chat_resize, add="+")
        self.show_empty_state()

    def create_log_page(self, parent):
        page = ctk.CTkFrame(parent, corner_radius=16, fg_color=CARD, border_width=1, border_color=BORDER)
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)
        self.pages["log"] = page

        self.log_textbox = ctk.CTkTextbox(
            page, font=(FONT_MONO, 12), fg_color="transparent", text_color=TEXT, wrap="word", corner_radius=16
        )
        self.log_textbox.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.log_textbox.tag_config("time", foreground="#8A94A6")
        self.log_textbox.tag_config("warning", foreground="#D97706")
        self.log_textbox.tag_config("error", foreground="#DC2626")
        self.log_textbox.configure(state="disabled")

    def create_settings_page(self, parent):
        page = ctk.CTkFrame(parent, fg_color="transparent")
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)
        self.pages["settings"] = page
        # The cards do not fit into a small window, so they live in a scrollable area
        scroll = ctk.CTkScrollableFrame(page, fg_color="transparent")
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)
        page = scroll

        # Microphone card
        mic_card = self.create_card(
            page, 0, "Микрофон", "Устройство, с которого ассистент слушает команды. Применяется сразу после выбора."
        )
        mic_row = ctk.CTkFrame(mic_card, fg_color="transparent")
        mic_row.pack(fill="x", padx=20, pady=(0, 6))
        self.device_options = self.get_input_devices()
        saved_device = self.get_device_by_index(getattr(self.assistant, "input_device", None))
        self.device_combobox = ctk.CTkComboBox(
            mic_row,
            values=self.device_options,
            font=(FONT, 13),
            dropdown_font=(FONT, 13),
            height=36,
            corner_radius=10,
            command=self.on_device_selected,
        )
        self.device_combobox.set(saved_device or self.get_default_input_device())
        self.device_combobox.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            mic_row,
            text="Обновить",
            width=100,
            height=36,
            corner_radius=10,
            font=(FONT, 13),
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            command=self.refresh_devices,
        ).pack(side="left", padx=(10, 0))
        self.device_feedback = ctk.CTkLabel(mic_card, text="", font=(FONT, 12), text_color=MUTED, anchor="w")
        self.device_feedback.pack(fill="x", padx=20, pady=(0, 14))

        # Microphone check card
        test_card = self.create_card(
            page,
            1,
            "Проверка микрофона",
            f"Нажмите «Проверить» и скажите несколько слов, например «раз, два, три». Тест длится "
            f"{MIC_TEST_SECONDS} секунд: покажет уровень сигнала и то, как ассистент вас распознаёт.",
        )
        test_row = ctk.CTkFrame(test_card, fg_color="transparent")
        test_row.pack(fill="x", padx=20, pady=(0, 6))
        self.mic_meter = ctk.CTkProgressBar(
            test_row, height=14, corner_radius=7, fg_color=BORDER, progress_color=ACCENT
        )
        self.mic_meter.set(0)
        self.mic_meter.pack(side="left", fill="x", expand=True)
        self.mic_test_button = ctk.CTkButton(
            test_row,
            text="Проверить",
            width=110,
            height=36,
            corner_radius=10,
            font=(FONT, 13),
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            command=self.toggle_mic_test,
        )
        self.mic_test_button.pack(side="left", padx=(14, 0))
        self.mic_test_verdict = ctk.CTkLabel(
            test_card, text="", font=(FONT, 13, "bold"), text_color=TEXT, anchor="w", justify="left", wraplength=620
        )
        self.mic_test_verdict.pack(fill="x", padx=20, pady=(6, 0))
        self.mic_test_text = ctk.CTkLabel(
            test_card, text="", font=(FONT, 12), text_color=MUTED, anchor="w", justify="left", wraplength=620
        )
        self.mic_test_text.pack(fill="x", padx=20, pady=(2, 14))

        # Appearance card
        look_card = self.create_card(page, 2, "Оформление", "Цветовая схема окна.")
        current = getattr(self.assistant, "appearance", "Dark")
        self.appearance_buttons = ctk.CTkSegmentedButton(
            look_card,
            values=list(APPEARANCE_LABELS),
            font=(FONT, 13),
            height=36,
            command=self.on_appearance_selected,
        )
        self.appearance_buttons.set(next((k for k, v in APPEARANCE_LABELS.items() if v == current), "Тёмная"))
        self.appearance_buttons.pack(anchor="w", padx=20, pady=(0, 18))

        # About card
        about_card = self.create_card(
            page,
            3,
            "О программе",
            "Распознавание речи работает офлайн (Vosk), ответы на свободные вопросы даёт локальная модель "
            "через LM Studio. Подробный журнал сохраняется в logs/assistant.log.",
        )
        ctk.CTkFrame(about_card, height=4, fg_color="transparent").pack()

    # Card with a title and a description for the settings page
    def create_card(self, parent, row, title, description):
        card = ctk.CTkFrame(parent, corner_radius=16, fg_color=CARD, border_width=1, border_color=BORDER)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 14))
        ctk.CTkLabel(card, text=title, font=(FONT, 16, "bold"), text_color=TEXT, anchor="w").pack(
            fill="x", padx=20, pady=(16, 0)
        )
        ctk.CTkLabel(
            card, text=description, font=(FONT, 12), text_color=MUTED, anchor="w", justify="left", wraplength=620
        ).pack(fill="x", padx=20, pady=(2, 12))
        return card

    # Hint shown in the empty dialog
    def show_empty_state(self):
        box = ctk.CTkFrame(self.chat, fg_color="transparent")
        box.pack(expand=True, fill="both", pady=(60, 0))
        ctk.CTkLabel(
            box, text=f"Скажите «{self.activation_word}»", font=(FONT, 22, "bold"), text_color=TEXT
        ).pack()
        ctk.CTkLabel(
            box, text="и произнесите команду. Например:", font=(FONT, 14), text_color=MUTED
        ).pack(pady=(4, 16))
        chips = ctk.CTkFrame(box, fg_color="transparent")
        chips.pack()
        for index, example in enumerate(EXAMPLE_COMMANDS):
            chip = ctk.CTkFrame(chips, corner_radius=16, fg_color=BG, border_width=1, border_color=BORDER)
            chip.grid(row=index // 3, column=index % 3, padx=5, pady=5)
            ctk.CTkLabel(chip, text=example, font=(FONT, 13), text_color=TEXT).pack(padx=14, pady=6)
        self.empty_state = box

    # ---------- Pages ----------

    def show_page(self, name):
        titles = {
            "dialog": ("Диалог", "Ваши команды и ответы ассистента"),
            "log": ("Журнал", "Что происходит внутри ассистента"),
            "settings": ("Настройки", "Микрофон и оформление"),
        }
        title, subtitle = titles[name]
        self.page_title.configure(text=title)
        self.page_subtitle.configure(text=subtitle)
        self.pages[name].tkraise()
        for key, button in self.nav_buttons.items():
            button.configure(fg_color=NAV_ACTIVE if key == name else "transparent")
        # The clear button belongs to the dialog and log pages
        if name == "dialog":
            self.clear_button.configure(command=self.clear_dialog)
            self.clear_button.grid()
        elif name == "log":
            self.clear_button.configure(command=self.clear_log)
            self.clear_button.grid()
        else:
            self.clear_button.grid_remove()

    # ---------- State ----------

    def apply_state(self, state):
        if state not in STATES:
            return
        self.state = state
        title, hint, color, _ = STATES[state]
        self.status_title.configure(text=title)
        self.status_hint.configure(text=hint.format(activation=self.activation_word))
        self.status_dot.configure(fg_color=color)
        self.pulse_phase = False

    # Soft blinking of the status dot while the assistant listens or speaks
    def pulse(self):
        if self.state in ("listening", "speaking"):
            self.pulse_phase = not self.pulse_phase
            color, dim = STATES[self.state][2:]
            self.status_dot.configure(fg_color=dim if self.pulse_phase else color)
        self.root.after(550, self.pulse)

    # ---------- Events from other threads ----------

    def poll_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "state":
                    self.apply_state(event[1])
                elif kind == "message":
                    self.append_message(event[1], event[2])
                elif kind == "log":
                    self.append_log(*event[1:])
                elif kind == "mic_level":
                    self.set_meter(event[1])
                elif kind == "mic_result":
                    self.finish_mic_test(event[1])
        except queue.Empty:
            pass
        self.root.after(50, self.poll_events)

    # ---------- Dialog ----------

    def append_message(self, role, text):
        text = (text or "").strip()
        if not text:
            return
        if self.empty_state is not None:
            self.empty_state.destroy()
            self.empty_state = None

        is_user = role == "user"
        if is_user:
            text = text[:1].upper() + text[1:]
        side = "e" if is_user else "w"

        row = ctk.CTkFrame(self.chat, fg_color="transparent")
        row.pack(fill="x", pady=(0, 10), padx=6)
        bubble = ctk.CTkFrame(row, corner_radius=16, fg_color=BUBBLE_USER if is_user else BUBBLE_BOT)
        bubble.pack(anchor=side)
        label = ctk.CTkLabel(
            bubble,
            text=text,
            font=(FONT, 14),
            text_color="#FFFFFF" if is_user else TEXT,
            wraplength=self.wrap_length,
            justify="left",
            anchor="w",
        )
        label.pack(padx=14, pady=10)
        self.message_labels.append(label)

        author = "Вы" if is_user else "Астра"
        ctk.CTkLabel(
            row,
            text=f"{author} · {datetime.now().strftime('%H:%M')}",
            font=(FONT, 11),
            text_color=MUTED,
        ).pack(anchor=side, padx=6, pady=(2, 0))
        self.root.after(30, lambda: self.chat._parent_canvas.yview_moveto(1.0))

    def on_chat_resize(self, event):
        wrap = max(240, int(event.width * 0.66))
        if abs(wrap - self.wrap_length) < 8:
            return
        self.wrap_length = wrap
        for label in self.message_labels:
            label.configure(wraplength=wrap)

    def clear_dialog(self):
        for child in self.chat.winfo_children():
            child.destroy()
        self.message_labels.clear()
        self.show_empty_state()

    # ---------- Log ----------

    def append_log(self, level, time_text, message):
        box = self.log_textbox
        box.configure(state="normal")
        box.insert("end", time_text + "  ", "time")
        tag = LOG_LEVEL_TAGS.get(level)
        if tag:
            box.insert("end", message + "\n", tag)
        else:
            box.insert("end", message + "\n")
        # Do not let the log grow endlessly
        lines = int(box.index("end-1c").split(".")[0])
        if lines > LOG_LINES_LIMIT:
            box.delete("1.0", f"{lines - LOG_LINES_LIMIT + 1}.0")
        box.see("end")
        box.configure(state="disabled")

    def clear_log(self):
        self.log_textbox.configure(state="normal")
        self.log_textbox.delete("1.0", "end")
        self.log_textbox.configure(state="disabled")

    # ---------- Settings ----------

    # Gets a list of available audio input devices
    def get_input_devices(self):
        try:
            devices = sd.query_devices()
        except Exception:
            devices = []
        input_devices = [
            f"{i}: {device['name']}" for i, device in enumerate(devices) if device['max_input_channels'] > 0
        ]
        return input_devices if input_devices else ["Нет доступных устройств ввода"]

    # Get device name by index
    def get_device_by_index(self, index):
        try:
            devices = sd.query_devices()
            if index is not None and 0 <= index < len(devices):
                return f"{index}: {devices[index]['name']}"
        except Exception:
            pass
        return None

    # Get default device name
    def get_default_input_device(self):
        try:
            default_input_device = sd.default.device[0]
            return self.get_device_by_index(default_input_device) or "Выберите устройство ввода"
        except Exception:
            return "Выберите устройство ввода"

    def refresh_devices(self):
        self.device_options = self.get_input_devices()
        self.device_combobox.configure(values=self.device_options)
        self.show_device_feedback(f"Найдено устройств: {len(self.device_options)}")

    # Index of the microphone chosen in the settings (None if nothing valid is selected)
    def selected_device_index(self):
        try:
            return int(self.device_combobox.get().split(":")[0])
        except ValueError:
            return getattr(self.assistant, "input_device", None)

    # Start the microphone check, or stop it if it is already running
    def toggle_mic_test(self):
        if self.mic_test_thread is not None and self.mic_test_thread.is_alive():
            self.mic_test_stop.set()
            return
        device = self.selected_device_index()
        recognizer = getattr(self.assistant, "speech_recognizer", None)
        model = getattr(recognizer, "model", None)
        self.mic_test_stop = threading.Event()
        self.meter_value = 0.0
        self.mic_meter.set(0)
        self.mic_test_button.configure(text="Остановить")
        self.mic_test_verdict.configure(text="Говорите…", text_color=TEXT)
        self.mic_test_text.configure(text="")
        stop_event = self.mic_test_stop

        def run():
            result = check_microphone(
                device,
                duration=MIC_TEST_SECONDS,
                model=model,
                on_level=lambda level: self.events.put(("mic_level", level)),
                stop_event=stop_event,
            )
            self.events.put(("mic_result", result))

        self.mic_test_thread = threading.Thread(target=run, daemon=True)
        self.mic_test_thread.start()

    # Level meter with a fast rise and a slow fall
    def set_meter(self, level):
        self.meter_value = max(level, self.meter_value * 0.75)
        self.mic_meter.set(self.meter_value)

    def finish_mic_test(self, result):
        self.mic_test_button.configure(text="Проверить")
        self.mic_test_verdict.configure(
            text=result.message, text_color=MIC_STATUS_COLORS.get(result.status, TEXT)
        )
        if result.status in ("ok", "quiet", "loud"):
            if result.text:
                self.mic_test_text.configure(text=f"Распознано: «{result.text}»")
            else:
                self.mic_test_text.configure(text="Речь не распознана: скажите несколько слов во время теста.")
        else:
            self.mic_test_text.configure(text="")
        if result.status in ("error", "cancelled", "silent"):
            self.meter_value = 0.0
            self.mic_meter.set(0)

    # Applies the selected microphone right away
    def on_device_selected(self, selected):
        try:
            index = int(selected.split(":")[0])
        except ValueError:
            self.show_device_feedback("Устройство не выбрано")
            return
        self.assistant.set_input_device(index)
        # The previous verdict belongs to another device
        if self.mic_test_thread is None or not self.mic_test_thread.is_alive():
            self.mic_meter.set(0)
            self.meter_value = 0.0
            self.mic_test_verdict.configure(text="")
            self.mic_test_text.configure(text="")
        self.update_mic_label(selected)
        self.show_device_feedback("Микрофон применён")
        logging.info("Выбрано устройство: %s", selected)

    def update_mic_label(self, device_name):
        if device_name and ":" in device_name:
            device_name = device_name.split(":", 1)[1].strip()
        self.mic_label.configure(text=device_name or "не выбран")

    def show_device_feedback(self, text):
        self.device_feedback.configure(text=text)
        self.root.after(3000, lambda: self.device_feedback.configure(text=""))

    def on_appearance_selected(self, label):
        mode = APPEARANCE_LABELS.get(label, "Dark")
        ctk.set_appearance_mode(mode)
        if hasattr(self.assistant, "set_appearance"):
            self.assistant.set_appearance(mode)

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
