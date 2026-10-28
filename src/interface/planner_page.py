"""The page "Дела": timers, alarms, reminders and the list of tasks."""
from datetime import datetime

import customtkinter as ctk

from src.interface import graphics as g
from src.interface import theme as t
from src.interface.pages import scrolling_page
from src.interface.widgets import Card, make_button
from src.productivity import ru_parse
from src.productivity.planner import ALARM, REMINDER, TIME_FORMAT, TIMER, seconds_left

QUICK_TIMERS = (("1 мин", 60), ("5 мин", 300), ("10 мин", 600), ("25 мин", 1500), ("1 час", 3600))
KIND_ICONS = {TIMER: "hourglass", ALARM: "clock", REMINDER: "bell"}
KIND_TITLES = {TIMER: "Таймер", ALARM: "Будильник", REMINDER: "Напоминание"}
TASK_TEXT_LIMIT = 80


def format_left(seconds, short=False):
    """A countdown: 04:32, 1:05:00 for a timer; "через 8 ч 40 мин" for an alarm."""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if short:
        days, hours = divmod(hours, 24)
        if days:
            return f"через {days} д {hours} ч"
        if hours:
            return f"через {hours} ч {minutes:02d} мин"
        return f"через {max(minutes, 1)} мин"
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def limited(text, limit=TASK_TEXT_LIMIT):
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


class PlannerPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.app = app
        self.planner = app.assistant.planner
        self.countdowns = {}
        body, _ = scrolling_page(self, "Дела", "Таймеры, будильники, напоминания и задачи: голосом или здесь")

        self._build_quick(body, 1)
        self._build_events(body, 2)
        self._build_tasks(body, 3)
        self._build_examples(body, 4)
        self.refresh()
        self.after(1000, self._tick)

    # ---------- Quick add ----------

    def _build_quick(self, body, row):
        card = Card(body, "Быстро добавить", "Напишите так же, как сказали бы голосом.")
        card.grid(row=row, column=0, sticky="ew", pady=(0, 14))
        line = ctk.CTkFrame(card, fg_color="transparent")
        line.pack(fill="x", padx=22, pady=(0, 10))
        line.grid_columnconfigure(0, weight=1)
        self.quick = ctk.CTkEntry(
            line, height=40, corner_radius=20, border_color=t.BORDER, fg_color=t.SURFACE_ALT, text_color=t.TEXT,
            placeholder_text="Напомни в 18:00 позвонить маме", placeholder_text_color=t.MUTED, font=(t.FONT, 14),
        )
        self.quick.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.quick.bind("<Return>", lambda _e: self.quick_add())
        make_button(line, "Добавить", self.quick_add, "primary", width=110, height=40).grid(row=0, column=1)

        chips = ctk.CTkFrame(card, fg_color="transparent")
        chips.pack(fill="x", padx=22, pady=(0, 6))
        ctk.CTkLabel(chips, text="Таймер:", font=(t.FONT, 12), text_color=t.MUTED).pack(side="left", padx=(0, 8))
        for caption, seconds in QUICK_TIMERS:
            make_button(chips, caption, lambda s=seconds: self.planner.add_timer(s), "secondary", width=70,
                        height=30, font_size=12).pack(side="left", padx=3)
        self.quick_answer = card.add_text("", color=t.MUTED, size=12, pady=(4, 14), collapse=True)

    def quick_add(self):
        text = self.quick.get().strip()
        if not text:
            return
        answer = self.app.assistant.quick_add(text)
        if answer is None:
            self.quick_answer.configure(
                text="Не поняла. Попробуйте: «таймер на 10 минут», «будильник на 7:30», "
                     "«напомни завтра в 9 купить хлеб», «добавь задачу купить молоко».",
                text_color=t.WARN,
            )
            return
        self.quick.delete(0, "end")
        self.quick_answer.configure(text=answer, text_color=t.MUTED)

    # ---------- Timers, alarms, reminders ----------

    def _build_events(self, body, row):
        self.events_card = Card(body, "Таймеры, будильники и напоминания")
        self.events_card.grid(row=row, column=0, sticky="ew", pady=(0, 14))
        self.events_box = ctk.CTkFrame(self.events_card, fg_color="transparent")
        self.events_box.pack(fill="x", padx=14, pady=(0, 12))
        self.events_box.grid_columnconfigure(1, weight=1)

    def _event_row(self, index, item):
        due = datetime.strptime(item["due"], TIME_FORMAT)
        if item["kind"] == TIMER:
            title = item["text"] or KIND_TITLES[TIMER]
            subtitle = f"на {ru_parse.describe_duration(item['length'], accusative=True)}" if item.get("length") else ""
        elif item["kind"] == ALARM:
            title = KIND_TITLES[ALARM] + (f" · {item['text']}" if item["text"] else "")
            subtitle = ru_parse.describe_moment(due)
        else:
            title = item["text"] or KIND_TITLES[REMINDER]
            subtitle = ru_parse.describe_moment(due)
        if item.get("repeat"):
            subtitle += " · " + ("каждый день" if item["repeat"] == "daily" else "по будням")
        ctk.CTkLabel(self.events_box, text="", image=g.icon(KIND_ICONS[item["kind"]], t.ACCENT, 22)).grid(
            row=index, column=0, padx=(10, 12), pady=8)
        texts = ctk.CTkFrame(self.events_box, fg_color="transparent")
        texts.grid(row=index, column=1, sticky="w")
        ctk.CTkLabel(texts, text=limited(title, 60), font=(t.FONT, 14, "bold"), text_color=t.TEXT, anchor="w").pack(
            anchor="w")
        if subtitle:
            ctk.CTkLabel(texts, text=subtitle, font=(t.FONT, 12), text_color=t.MUTED, anchor="w").pack(anchor="w")
        left = ctk.CTkLabel(self.events_box, text="", font=(t.FONT_MONO if item["kind"] == TIMER else t.FONT, 15 if
                            item["kind"] == TIMER else 12), text_color=t.TEXT if item["kind"] == TIMER else t.MUTED)
        left.grid(row=index, column=2, padx=(10, 6), sticky="e")
        self.countdowns[item["id"]] = (left, item)
        ctk.CTkButton(
            self.events_box, text="", image=g.icon("close", t.MUTED, 16), width=32, height=32, corner_radius=16,
            fg_color="transparent", hover_color=t.HOVER, command=lambda i=item["id"]: self.planner.remove(i),
        ).grid(row=index, column=3, padx=(0, 8))

    def _tick(self):
        self._tick_once()
        self.after(1000, self._tick)

    # ---------- Tasks ----------

    def _build_tasks(self, body, row):
        card = Card(body, "Задачи", "Отметьте галочкой, когда сделано. Голосом: «добавь задачу …», «выполни задачу два».")
        card.grid(row=row, column=0, sticky="ew", pady=(0, 14))
        line = ctk.CTkFrame(card, fg_color="transparent")
        line.pack(fill="x", padx=22, pady=(0, 8))
        line.grid_columnconfigure(0, weight=1)
        self.task_entry = ctk.CTkEntry(
            line, height=40, corner_radius=20, border_color=t.BORDER, fg_color=t.SURFACE_ALT, text_color=t.TEXT,
            placeholder_text="Новая задача", placeholder_text_color=t.MUTED, font=(t.FONT, 14),
        )
        self.task_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.task_entry.bind("<Return>", lambda _e: self.add_task())
        make_button(line, "Добавить", self.add_task, "primary", width=110, height=40).grid(row=0, column=1)
        self.tasks_box = ctk.CTkFrame(card, fg_color="transparent")
        self.tasks_box.pack(fill="x", padx=14, pady=(0, 4))
        self.tasks_box.grid_columnconfigure(0, weight=1)
        self.clear_done_button = make_button(card, "Убрать выполненные", self.planner.clear_done, "secondary",
                                             width=190, height=32, font_size=12)
        self.tasks_footer = ctk.CTkFrame(card, height=10, fg_color="transparent")
        self.tasks_footer.pack()

    def add_task(self):
        text = self.task_entry.get().strip()
        if text:
            self.planner.add_task(text)
            self.task_entry.delete(0, "end")

    def _task_row(self, index, task, number):
        done = task["done"]
        check = ctk.CTkCheckBox(
            self.tasks_box, text=limited(f"{number}. {task['text']}" if number else task["text"]), width=20,
            font=ctk.CTkFont(family=t.FONT, size=14, overstrike=done), text_color=t.MUTED if done else t.TEXT,
            fg_color=t.ACCENT, hover_color=t.ACCENT_HOVER, border_color=t.MUTED, checkmark_color=t.ACCENT_TEXT,
            corner_radius=7, command=lambda i=task["id"], d=done: self.planner.set_done(i, not d),
        )
        if done:
            check.select()
        check.grid(row=index, column=0, sticky="w", padx=(12, 0), pady=7)
        ctk.CTkButton(
            self.tasks_box, text="", image=g.icon("close", t.MUTED, 16), width=32, height=32, corner_radius=16,
            fg_color="transparent", hover_color=t.HOVER, command=lambda i=task["id"]: self.planner.remove_task(i),
        ).grid(row=index, column=1, padx=(0, 8))

    # ---------- Examples ----------

    def _build_examples(self, body, row):
        card = Card(body, "Что можно сказать", "Все эти фразы работают и голосом, и в поле ввода на главной странице.")
        card.grid(row=row, column=0, sticky="ew", pady=(0, 14))
        for phrase, note in t.PLANNER_EXAMPLES:
            card.add_text(f"«{phrase}»", size=13, bold=True, pady=(4, 0))
            card.add_text(note, color=t.MUTED, size=12, pady=(0, 2))
        ctk.CTkFrame(card, height=12, fg_color="transparent").pack()

    # ---------- Redraw ----------

    def refresh(self):
        for child in self.events_box.winfo_children():
            child.destroy()
        self.countdowns = {}
        items = self.planner.items()
        if not items:
            ctk.CTkLabel(self.events_box, text="Пока ничего нет. Скажите «таймер на десять минут» или нажмите кнопку выше.",
                         font=(t.FONT, 13), text_color=t.MUTED, anchor="w", wraplength=520, justify="left").grid(
                row=0, column=0, columnspan=4, sticky="w", padx=12, pady=10)
        for index, item in enumerate(items):
            self._event_row(index, item)
        self._tick_once()

        for child in self.tasks_box.winfo_children():
            child.destroy()
        tasks = self.planner.tasks()
        if not tasks:
            ctk.CTkLabel(self.tasks_box, text="Список задач пуст.", font=(t.FONT, 13), text_color=t.MUTED,
                         anchor="w").grid(row=0, column=0, sticky="w", padx=12, pady=10)
        number = 0
        for index, task in enumerate(tasks):
            if not task["done"]:
                number += 1
            self._task_row(index, task, None if task["done"] else number)
        if any(task["done"] for task in tasks):
            self.clear_done_button.pack(anchor="w", padx=22, pady=(4, 8), before=self.tasks_footer)
        else:
            self.clear_done_button.pack_forget()

    def _tick_once(self):
        now = datetime.now().replace(microsecond=0)
        for label, item in self.countdowns.values():
            left = seconds_left(item, now)
            label.configure(text=format_left(left) if item["kind"] == TIMER else format_left(left, short=True))
