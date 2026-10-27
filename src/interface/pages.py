"""The pages Команды, Модель, Журнал and Настройки."""
import customtkinter as ctk
from tkinter import messagebox

from src.interface import theme as t
from src.interface.widgets import Card, make_button, page_header

PAGE_PADDING = 24


def scrolling_page(page, title, subtitle):
    """A page with a header and a scrollable body; returns (body, header)."""
    page.grid_columnconfigure(0, weight=1)
    page.grid_rowconfigure(0, weight=1)
    scroll = ctk.CTkScrollableFrame(page, fg_color="transparent", corner_radius=0)
    scroll.grid(row=0, column=0, sticky="nsew", padx=PAGE_PADDING)
    scroll.grid_columnconfigure(0, weight=1)
    header = page_header(scroll, title, subtitle)
    header.grid(row=0, column=0, sticky="ew", pady=(18, 14))
    return scroll, header


class CommandsPage(ctk.CTkFrame):
    """Everything the assistant can do: the phrases of each command and a button to run it."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.app = app
        body, header = scrolling_page(self, "Команды", "Что умеет ассистент: скажите фразу или запустите кнопкой")
        self.search = ctk.CTkEntry(
            header, placeholder_text="Поиск по командам", width=260, height=38, corner_radius=19,
            border_color=t.BORDER, fg_color=t.SURFACE, text_color=t.TEXT, placeholder_text_color=t.MUTED,
        )
        self.search.grid(row=0, column=1, rowspan=2, sticky="e")
        self.search.bind("<KeyRelease>", lambda _e: self.apply_filter())
        self.body = body
        self.rows = []
        commands = getattr(app.assistant, "commands", [])
        for index, command in enumerate(commands, start=1):
            self.rows.append(self._build_row(index, command))
        self.empty = ctk.CTkLabel(body, text="Ничего не найдено", font=(t.FONT, 13), text_color=t.MUTED)

    def _build_row(self, grid_row, command):
        triggers = command.get("triggers", [])
        frame = ctk.CTkFrame(self.body, corner_radius=16, fg_color=t.SURFACE, border_width=1, border_color=t.BORDER)
        frame.grid(row=grid_row, column=0, sticky="ew", pady=(0, 8))
        frame.grid_columnconfigure(0, weight=1)
        title = command.get("say") or command.get("command", "")
        ctk.CTkLabel(frame, text=title or command.get("command", ""), font=(t.FONT, 14, "bold"),
                     text_color=t.TEXT, anchor="w").grid(row=0, column=0, sticky="w", padx=18, pady=(12, 0))
        phrases = " · ".join(f"«{p.strip()}»" for p in triggers)
        label = ctk.CTkLabel(frame, text=phrases, font=(t.FONT, 12), text_color=t.MUTED, anchor="w",
                             justify="left", wraplength=620)
        label.grid(row=1, column=0, sticky="w", padx=18, pady=(2, 12))
        frame.bind("<Configure>", lambda e, l=label: l.configure(wraplength=max(200, e.width - 170)), add="+")
        if triggers:
            make_button(
                frame, "Выполнить", lambda c=command: self.app.run_command(c), "secondary", width=110, height=32,
            ).grid(row=0, column=1, rowspan=2, padx=16)
        search_text = " ".join([command.get("command", ""), command.get("say", "")] + triggers).lower()
        return frame, search_text

    def apply_filter(self):
        query = self.search.get().strip().lower()
        visible = 0
        for frame, text in self.rows:
            if query in text:
                frame.grid()
                visible += 1
            else:
                frame.grid_remove()
        if visible:
            self.empty.grid_remove()
        else:
            self.empty.grid(row=len(self.rows) + 2, column=0, pady=30)


class ModelPage(ctk.CTkFrame):
    """The models that work inside the assistant."""

    BIG_STATES = {
        "none": ("не установлена", t.OFF, "Скачайте модель командой scripts/download_vosk_model.py — "
                 "она заметно точнее распознаёт команды."),
        "pending": ("ожидает загрузки", t.WARN, "Загрузка начнётся, когда ассистент освободится."),
        "loading": ("загружается", t.WARN, "Модель загружается в фоне, ассистент уже работает."),
        "ready": ("работает", t.OK, "Команды распознаёт большая модель, слово активации — маленькая."),
        "failed": ("не загрузилась", t.ERROR, "Подробности смотрите в журнале. Работает маленькая модель."),
    }

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.app = app
        body, _ = scrolling_page(self, "Модель", "Какие нейросети работают внутри ассистента")
        self.small = self._card(body, 1, "Распознавание: слово активации",
                                "Маленькая модель Vosk. Всегда в памяти, быстро реагирует на «Астра».")
        self.big = self._card(body, 2, "Распознавание: команды", "")
        self.llm = self._card(body, 3, "Ответы на вопросы",
                              "Локальная языковая модель через LM Studio, работает без интернета. "
                              "Нужен запущенный LM Studio с загруженной моделью.")
        self.refresh()

    def _card(self, parent, row, title, description):
        card = Card(parent, title, description)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 14))
        name = card.add_text("", size=14, bold=True, pady=(0, 2))
        note = card.add_text("", color=t.MUTED, size=12, pady=(0, 16))
        return name, note

    def refresh(self):
        info = self.app.assistant.models_info()
        self.small[0].configure(text=info["small_model"], text_color=t.TEXT)
        self.small[1].configure(text="")
        title, color, hint = self.BIG_STATES.get(info["big_model_state"], self.BIG_STATES["none"])
        name = info["big_model"] or "vosk-model-ru-0.42"
        self.big[0].configure(text=f"{name} — {title}", text_color=color)
        self.big[1].configure(text=hint)
        self.llm[0].configure(text=info["llm"], text_color=t.TEXT)
        self.llm[1].configure(text="")


class LogPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        header = page_header(self, "Журнал", "Что происходит внутри ассистента")
        header.grid(row=0, column=0, sticky="ew", padx=PAGE_PADDING, pady=(18, 14))
        make_button(header, "Очистить", self.clear, "secondary", width=110, height=34).grid(
            row=0, column=1, rowspan=2, sticky="e"
        )
        frame = ctk.CTkFrame(self, corner_radius=18, fg_color=t.SURFACE, border_width=1, border_color=t.BORDER)
        frame.grid(row=1, column=0, sticky="nsew", padx=PAGE_PADDING, pady=(0, 20))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        self.box = ctk.CTkTextbox(frame, font=(t.FONT_MONO, 12), fg_color="transparent", text_color=t.TEXT,
                                  wrap="word", corner_radius=18)
        self.box.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.box.tag_config("time", foreground="#9A8480")
        self.box.tag_config("warning", foreground="#D97706")
        self.box.tag_config("error", foreground="#DC2626")
        self.box.configure(state="disabled")

    def append(self, level, time_text, message):
        box = self.box
        box.configure(state="normal")
        box.insert("end", time_text + "  ", "time")
        tag = t.LOG_LEVEL_TAGS.get(level)
        if tag:
            box.insert("end", message + "\n", tag)
        else:
            box.insert("end", message + "\n")
        # Do not let the log grow endlessly
        lines = int(box.index("end-1c").split(".")[0])
        if lines > t.LOG_LINES_LIMIT:
            box.delete("1.0", f"{lines - t.LOG_LINES_LIMIT + 1}.0")
        box.see("end")
        box.configure(state="disabled")

    def clear(self):
        self.box.configure(state="normal")
        self.box.delete("1.0", "end")
        self.box.configure(state="disabled")


class SettingsPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.app = app
        body, _ = scrolling_page(self, "Настройки", "Оформление и сведения о программе")

        look = Card(body, "Оформление", "Цветовая схема окна.")
        look.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        row = ctk.CTkFrame(look, fg_color="transparent")
        row.pack(anchor="w", padx=22, pady=(0, 18))
        self.appearance_buttons = {}
        for column, label in enumerate(t.APPEARANCE_LABELS):
            button = make_button(row, label, lambda value=label: self.on_appearance(value), "secondary", width=116)
            button.grid(row=0, column=column, padx=(0, 8))
            self.appearance_buttons[label] = button
        current = getattr(app.assistant, "appearance", "Dark")
        self.mark_appearance(next((k for k, v in t.APPEARANCE_LABELS.items() if v == current), "Тёмная"))

        about = Card(
            body, "О программе",
            "Распознавание речи работает офлайн (Vosk), ответы на свободные вопросы даёт локальная модель через "
            "LM Studio. Подробный журнал сохраняется в logs/assistant.log.",
        )
        about.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        ctk.CTkFrame(about, height=6, fg_color="transparent").pack()

    # The chosen scheme is a filled button, the others are outlined
    def mark_appearance(self, chosen):
        for label, button in self.appearance_buttons.items():
            active = label == chosen
            button.configure(
                fg_color=t.ACCENT if active else "transparent", text_color=t.ACCENT_TEXT if active else t.TEXT,
                hover_color=t.ACCENT_HOVER if active else t.HOVER, border_width=0 if active else 1,
            )

    def on_appearance(self, label):
        self.mark_appearance(label)
        mode = t.APPEARANCE_LABELS.get(label, "Dark")
        ctk.set_appearance_mode(mode)
        if hasattr(self.app.assistant, "set_appearance"):
            self.app.assistant.set_appearance(mode)
        self.app.on_appearance_changed()


def confirm_risky(parent, command):
    """Ask before a command that changes the state of the computer; returns True when it may run."""
    effect = t.RISKY_COMMANDS.get(command.get("command"))
    if effect is None:
        return True
    return messagebox.askyesno("Подтверждение", f"Эта команда {effect}. Выполнить?", parent=parent)
