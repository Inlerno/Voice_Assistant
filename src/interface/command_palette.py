"""The quick search (Ctrl+K): pages, actions, settings of the look and commands of the assistant in one list.

What is typed finds the entries even with a typo; the last entry of the list always sends the typed text to the
assistant as a question, so the same field serves for commands and for talking.
"""
from dataclasses import dataclass
from typing import Callable

import customtkinter as ctk
from rapidfuzz import fuzz

from src.interface import graphics as g
from src.interface import theme as t
from src.interface.widgets import scale_of

WIDTH = 600
MAX_ROWS = 9
TYPO_LIMIT = 76          # how alike (percent) a word has to be to count as a typo of a word of the entry


@dataclass
class Entry:
    title: str
    kind: str                 # a short note on the right: "Страница · Ctrl+2", "Команда" ...
    icon: str
    action: Callable
    keywords: str = ""


def score(query, entry):
    """How well an entry answers what is typed: 0 means not at all, a bigger number is a better match."""
    words = query.lower().split()
    if not words:
        return 1
    title = entry.title.lower()
    text = f"{title} {entry.keywords.lower()}"
    joined = " ".join(words)
    if title.startswith(joined):
        return 100
    if joined in title:
        return 90
    if all(word in text for word in words):
        return 80
    # A typo in a word: every typed word has to look like a word of the entry
    known = text.replace("·", " ").split()
    total = 0
    for word in words:
        best = max((fuzz.ratio(word, other) for other in known), default=0)
        if best < TYPO_LIMIT:
            return 0
        total += best
    return int(total / len(words) * 0.7)


def search(entries, query, limit=MAX_ROWS):
    """The entries for the query, the best first (those that came earlier win when the match is the same)."""
    if not query.strip():
        return list(entries[:limit])
    ranked = sorted(((score(query, entry), -index, entry) for index, entry in enumerate(entries)),
                    key=lambda item: item[:2], reverse=True)
    return [entry for found, _, entry in ranked if found > 0][:limit]


class Row(ctk.CTkFrame):
    """A line of the list: an icon, the title and a note on the right."""

    def __init__(self, master, on_click, on_hover):
        super().__init__(master, corner_radius=t.r(12), fg_color="transparent", height=42)
        self.grid_columnconfigure(1, weight=1)
        self.icon = ctk.CTkLabel(self, text="", width=22)
        self.icon.grid(row=0, column=0, padx=(12, 10), pady=9)
        self.title = ctk.CTkLabel(self, text="", font=(t.FONT, 14), text_color=t.TEXT, anchor="w")
        self.title.grid(row=0, column=1, sticky="ew")
        self.kind = ctk.CTkLabel(self, text="", font=(t.FONT, 11), text_color=t.MUTED, anchor="e")
        self.kind.grid(row=0, column=2, padx=(10, 14))
        for widget in (self, self.icon, self.title, self.kind):
            widget.bind("<Button-1>", lambda _e: on_click(self), add="+")
            widget.bind("<Enter>", lambda _e: on_hover(self), add="+")
        self.entry = None

    def show(self, entry, selected):
        self.entry = entry
        self.icon.configure(image=g.icon(entry.icon, t.ACCENT_VIVID if selected else t.MUTED, 20))
        self.title.configure(text=entry.title, font=(t.FONT, 14, "bold") if selected else (t.FONT, 14))
        self.kind.configure(text=entry.kind)
        self.configure(fg_color=t.ACCENT_SOFT if selected else "transparent")


class CommandPalette(ctk.CTkFrame):
    """The window of the search: it lies over the page near the top edge and is closed by Esc or by a click outside."""

    def __init__(self, root, app):
        super().__init__(root, width=WIDTH, corner_radius=t.r(20), fg_color=t.SURFACE, border_width=1,
                         border_color=t.BORDER)
        self.root = root
        self.app = app
        self.entries = []
        self.found = []
        self.selected = 0
        self.last_query = None
        # A frame takes the size of what lies in it, so the width is held by the column (in real pixels)
        self.grid_columnconfigure(0, weight=1, minsize=int(WIDTH * scale_of(self)))

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        top.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(top, text="", image=g.icon("search", t.MUTED, 20)).grid(row=0, column=0, padx=(8, 6))
        self.field = ctk.CTkEntry(
            top, placeholder_text="Страница, настройка или команда…", border_width=0, fg_color="transparent",
            height=42, font=(t.FONT, 16), text_color=t.TEXT, placeholder_text_color=t.MUTED,
        )
        self.field.grid(row=0, column=1, sticky="ew")
        ctk.CTkLabel(top, text="Esc", font=(t.FONT, 11), text_color=t.MUTED, fg_color=t.SURFACE_ALT,
                     corner_radius=t.r(6), width=34, height=22).grid(row=0, column=2, padx=(8, 6))
        ctk.CTkFrame(self, height=1, corner_radius=0, fg_color=t.BORDER).grid(row=1, column=0, sticky="ew")

        self.list = ctk.CTkFrame(self, fg_color="transparent")
        self.list.grid(row=2, column=0, sticky="ew", padx=8, pady=(8, 4))
        self.list.grid_columnconfigure(0, weight=1)
        self.rows = [Row(self.list, self.run_row, self.hover_row) for _ in range(MAX_ROWS + 1)]
        self.footer = ctk.CTkLabel(self, text="↑ ↓ выбрать   ·   Enter выполнить   ·   Ctrl+K закрыть",
                                   font=(t.FONT, 11), text_color=t.MUTED)
        self.footer.grid(row=3, column=0, pady=(2, 10))

        entry = self.field._entry
        entry.bind("<KeyRelease>", self._on_key, add="+")
        entry.bind("<Down>", lambda _e: self.move(1) or "break")
        entry.bind("<Up>", lambda _e: self.move(-1) or "break")
        entry.bind("<Return>", lambda _e: self.run_selected() or "break")
        entry.bind("<Escape>", lambda _e: self.close() or "break")

    # ---------- Opening and closing ----------

    @property
    def is_open(self):
        return bool(self.winfo_ismapped())

    def open(self, entries):
        self.entries = entries
        self.field.delete(0, "end")
        self.last_query = None
        self.refresh()
        self.place(relx=0.5, y=56, anchor="n")
        self.lift()
        self.field.focus_set()

    def close(self):
        if not self.is_open:
            return
        self.place_forget()
        self.root.focus_set()

    def contains(self, widget):
        return str(widget).startswith(str(self))

    # ---------- The list ----------

    def _on_key(self, event):
        if event.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
            return
        if self.field.get() != self.last_query:
            self.refresh()

    def refresh(self):
        query = self.field.get()
        self.last_query = query
        self.found = search(self.entries, query)
        text = query.strip()
        if text:
            self.found.append(Entry(f"Спросить Астру: «{text}»", "Вопрос", "sparkle",
                                    lambda: self.app.submit_text(text)))
        self.selected = 0
        self.paint()

    def paint(self):
        for index, row in enumerate(self.rows):
            if index < len(self.found):
                row.show(self.found[index], index == self.selected)
                row.grid(row=index, column=0, sticky="ew", pady=1)
            else:
                row.grid_remove()

    def move(self, step):
        if self.found:
            self.selected = (self.selected + step) % len(self.found)
            self.paint()

    def hover_row(self, row):
        index = self.rows.index(row)
        if index != self.selected and index < len(self.found):
            self.selected = index
            self.paint()

    def run_row(self, row):
        if row.entry is not None:
            self.selected = self.rows.index(row)
            self.run_selected()

    def run_selected(self):
        if not self.found:
            return
        entry = self.found[self.selected]
        self.close()
        # After the window is gone, so the action can change the window as it likes
        self.root.after(20, entry.action)
