"""The page "Приложения": the programs the assistant can start by voice."""
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from src.interface import graphics as g
from src.interface import theme as t
from src.interface.pages import scrolling_page
from src.interface.widgets import make_button
from src.productivity.apps import normalize

SOURCE_TITLES = {"user": "добавлено вами", "builtin": "встроено в Windows", "menu": "из меню «Пуск»"}
ROWS_LIMIT = 60


class AppsPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.app = app
        self.launcher = app.assistant.apps
        body, header = scrolling_page(
            self, "Приложения", "Скажите «открой калькулятор» или нажмите кнопку. Программы из меню «Пуск» найдутся сами."
        )
        make_button(header, "Добавить программу", self.add_app, "primary", width=170, height=36).grid(
            row=0, column=1, rowspan=2, sticky="e")
        self.search = ctk.CTkEntry(
            body, height=40, corner_radius=20, border_color=t.BORDER, fg_color=t.SURFACE, text_color=t.TEXT,
            placeholder_text="Поиск по программам", placeholder_text_color=t.MUTED, font=(t.FONT, 14),
        )
        self.search.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        self.search.bind("<KeyRelease>", lambda _e: self.refresh())
        self.list = ctk.CTkFrame(body, fg_color="transparent")
        self.list.grid(row=2, column=0, sticky="ew")
        self.list.grid_columnconfigure(0, weight=1)
        self.loaded = False
        self.bind("<Map>", self._on_show)

    def _on_show(self, _event=None):
        # The Start menu is read when the page is opened for the first time
        if not self.loaded:
            self.loaded = True
            self.refresh()

    def refresh(self):
        if not self.loaded:
            return
        for child in self.list.winfo_children():
            child.destroy()
        query = normalize(self.search.get())
        shown = 0
        total = 0
        for entry in self.launcher.all_apps():
            if query and query not in normalize(entry["name"]) and not any(query in normalize(a) for a in entry["aliases"]):
                continue
            total += 1
            if shown < ROWS_LIMIT:
                self._row(shown, entry)
                shown += 1
        if not shown:
            ctk.CTkLabel(self.list, text="Ничего не найдено", font=(t.FONT, 13), text_color=t.MUTED).grid(
                row=0, column=0, pady=30)
        elif total > shown:
            ctk.CTkLabel(self.list, text=f"Показано {shown} из {total}: уточните поиск", font=(t.FONT, 12),
                         text_color=t.MUTED).grid(row=shown, column=0, pady=12)

    def _row(self, index, entry):
        frame = ctk.CTkFrame(self.list, corner_radius=16, fg_color=t.SURFACE, border_width=1, border_color=t.BORDER)
        frame.grid(row=index, column=0, sticky="ew", pady=(0, 8))
        frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(frame, text="", image=g.icon("grid", t.ACCENT, 22)).grid(row=0, column=0, rowspan=2, padx=(16, 12),
                                                                              pady=12)
        ctk.CTkLabel(frame, text=entry["name"], font=(t.FONT, 14, "bold"), text_color=t.TEXT, anchor="w").grid(
            row=0, column=1, sticky="w", pady=(10, 0))
        ctk.CTkLabel(frame, text=f"«открой {entry['name'].lower()}» · {SOURCE_TITLES[entry['source']]}",
                     font=(t.FONT, 12), text_color=t.MUTED, anchor="w").grid(row=1, column=1, sticky="w", pady=(0, 10))
        buttons = ctk.CTkFrame(frame, fg_color="transparent")
        buttons.grid(row=0, column=2, rowspan=2, padx=14)
        if entry["source"] == "user":
            make_button(buttons, "Убрать", lambda n=entry["name"]: self.remove_app(n), "ghost", width=80,
                        height=32, font_size=12).pack(side="left", padx=(0, 6))
        make_button(buttons, "Открыть", lambda e=entry: self.open_app(e), "secondary", width=100, height=32).pack(
            side="left")

    def open_app(self, entry):
        try:
            self.launcher.open(entry)
        except OSError as error:
            messagebox.showerror("Не получилось открыть", f"{entry['name']}: {error}", parent=self)

    def add_app(self):
        path = filedialog.askopenfilename(
            parent=self, title="Выберите программу или ярлык",
            filetypes=[("Программы и ярлыки", "*.exe *.lnk *.bat *.cmd"), ("Все файлы", "*.*")],
        )
        if not path:
            return
        dialog = ctk.CTkInputDialog(text="Как её называть голосом? Например: Фотошоп", title="Название")
        name = (dialog.get_input() or "").strip()
        if not name:
            name = Path(path).stem
        self.launcher.add_app(name, path)
        self.refresh()

    def remove_app(self, name):
        if messagebox.askyesno("Убрать из списка", f"Убрать «{name}» из списка? Сама программа не удалится.",
                               parent=self):
            self.launcher.remove_app(name)
            self.refresh()
