"""The settings of the speech recognition: the engine that understands the commands (Vosk or Whisper), the models of
Whisper that can be downloaded, and the language Whisper listens for. A part of the settings page (the tab
"Распознавание")."""
import threading
from tkinter import messagebox

import customtkinter as ctk

from src.interface import graphics as g
from src.interface import theme as t
from src.interface.widgets import Card, Segmented, SettingRow, divider, make_button, make_progress, spacer
from src.speech.stt_settings import LANGUAGES, VOSK, WHISPER
from src.speech.whisper_engine import CATALOG, SttCancelled, SttError, catalog_entry

ENGINE_OPTIONS = ((VOSK, "Vosk"), (WHISPER, "Whisper"))
# The progress bar is moved after this share of the download, not after every piece
PROGRESS_STEP = 0.004


def size_text(megabytes):
    return f"{megabytes / 1024:.1f} ГБ".replace(".", ",") if megabytes >= 1024 else f"{megabytes} МБ"


class SpeechSection(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.app = app
        self.stt = app.assistant.stt
        # A download goes on when the window is built again: its cancel switch lives in the application
        self.cancel_events = app.stt_downloads
        self.busy = {}
        # How far every download has come: a bar that is made again (the tab is shown again) starts from there
        self.fractions = {}
        self.grid_columnconfigure(0, weight=1)
        info = self.stt.info()

        engine = Card(self, "Распознавание команд",
                      "Что превращает вашу речь в текст команды. Слово активации «Астра» всегда слушает Vosk: он "
                      "лёгкий и работает постоянно. Выбор относится к командам после него и к голосовым из Telegram.")
        engine.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        row = SettingRow(engine, "Движок", "Vosk быстрый и лёгкий, слова видны по мере речи. Whisper намного точнее, "
                                           "но тяжелее: после фразы нужна пауза на распознавание.")
        row.pack(fill="x", padx=22)
        self.engine_switch = Segmented(row.control, ENGINE_OPTIONS, info["engine"], self.choose_engine,
                                       button_width=110)
        self.engine_switch.pack()
        divider(engine, pady=(12, 10))
        self.status = engine.add_text("", size=13, bold=True, pady=(0, 2))
        self.note = engine.add_text("", color=t.MUTED, size=12, pady=(0, 16))

        models = Card(self, "Модели Whisper",
                      "Скачайте модель и нажмите «Использовать». Чем больше модель, тем точнее она понимает речь "
                      "и тем дольше думает: скорость зависит от процессора. Модели хранятся в папке data/stt/whisper.")
        models.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        language = SettingRow(models, "Язык речи", "На каком языке вы говорите. «Определять по речи» подходит, если "
                                                   "вы говорите на разных языках, но на коротких фразах ошибается.")
        language.pack(fill="x", padx=22)
        self.language_names = dict(LANGUAGES)
        self.language_menu = ctk.CTkOptionMenu(
            language.control, values=list(self.language_names.values()), width=210, height=36,
            corner_radius=t.pill(36), font=(t.FONT, 13), dropdown_font=(t.FONT, 13), fg_color=t.SURFACE_ALT,
            button_color=t.SURFACE_ALT, button_hover_color=t.HOVER, text_color=t.TEXT, dropdown_fg_color=t.SURFACE,
            dropdown_text_color=t.TEXT, dropdown_hover_color=t.ACCENT_SOFT, command=self.choose_language,
        )
        self.language_menu.pack()
        divider(models, pady=(12, 12))
        self.catalog_box = ctk.CTkFrame(models, fg_color="transparent")
        self.catalog_box.pack(fill="x", padx=22)
        self.catalog_box.grid_columnconfigure(0, weight=1)
        spacer(models, 14)
        self.refresh()

    # ---------- What is shown ----------

    def refresh(self):
        info = self.stt.info()
        self.engine_switch.set(info["engine"])
        self.language_menu.set(self.language_names.get(info["language"], self.language_names["ru"]))
        text, color, note = self._status(info)
        self.status.configure(text=text, text_color=color)
        self.note.configure(text=note)
        self._refresh_catalog(info)

    def _status(self, info):
        """The line that tells what recognizes the commands now, its color, and what the person can do about it."""
        if info["engine"] == VOSK:
            return "Сейчас команды распознаёт Vosk.", t.OK, "Чтобы попробовать Whisper, скачайте модель ниже."
        model = info["whisper_model"]
        if not model:
            return "Whisper ждёт модель.", t.WARN, ("Скачайте модель ниже и нажмите «Использовать». Пока команды "
                                                    "распознаёт Vosk.")
        name = self.title_of(model)
        state = info["whisper_state"]
        if state == "ready":
            return (f"Сейчас команды распознаёт {name}.", t.OK,
                    "Проверить, как он вас понимает, можно на странице «Голос» (проверка микрофона).")
        if state == "loading":
            return f"{name} загружается…", t.WARN, "Модель загружается в фоне (до минуты). Пока команды распознаёт Vosk."
        if state == "failed":
            return (f"{name} не загрузился.", t.ERROR,
                    "Подробности в журнале. Команды распознаёт Vosk. Попробуйте скачать модель заново.")
        return f"{name}…", t.WARN, ""

    def title_of(self, model_id):
        entry = catalog_entry(model_id)
        return f"Whisper {entry.title if entry else model_id}"

    def _refresh_catalog(self, info):
        self.busy.clear()
        for child in self.catalog_box.winfo_children():
            child.destroy()
        installed = info["installed"]
        listed = [(entry.id, entry) for entry in CATALOG]
        # A model that was put into the folder by hand is listed too
        listed += [(name, None) for name in installed if catalog_entry(name) is None]
        for index, (model_id, entry) in enumerate(listed):
            self._model_row(index, model_id, entry, model_id in installed, info)

    def _model_row(self, index, model_id, entry, installed, info):
        chosen = info["whisper_model"] == model_id and info["engine"] == WHISPER
        frame = ctk.CTkFrame(self.catalog_box, corner_radius=t.r(16), fg_color=t.SURFACE_ALT, border_width=1,
                             border_color=t.ACCENT_VIVID if chosen else t.BORDER)
        frame.grid(row=index, column=0, sticky="ew", pady=(0, 8))
        frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(frame, text="", image=g.icon("mic", t.MUTED, 22)).grid(row=0, column=0, rowspan=2, padx=(16, 12),
                                                                          pady=12)
        title = entry.title if entry else model_id
        ctk.CTkLabel(frame, text=title, font=(t.FONT, 14, "bold"), text_color=t.TEXT, anchor="w").grid(
            row=0, column=1, sticky="w", pady=(10, 0))
        if entry:
            size = f"скачать {size_text(entry.size_mb)}, на диске ≈ {size_text(entry.disk_mb)}"
            details = f"{entry.quality} · {entry.speed} · {size}"
        else:
            details = "Добавлена вручную"
        ctk.CTkLabel(frame, text=details, font=(t.FONT, 12), text_color=t.MUTED, anchor="w", justify="left",
                     wraplength=380).grid(row=1, column=1, sticky="w", pady=(0, 10))
        cell = ctk.CTkFrame(frame, fg_color="transparent")
        cell.grid(row=0, column=2, rowspan=2, padx=12)
        if model_id in self.cancel_events:
            bar = make_progress(cell, width=130)
            bar.set(self.fractions.get(model_id, 0))
            bar.pack(side="left", padx=(0, 8))
            self.busy[model_id] = bar
            make_button(cell, "Отмена", lambda i=model_id: self.cancel_events[i].set(), "ghost", width=80, height=32,
                        font_size=12).pack(side="left")
        elif installed:
            make_button(cell, "Удалить", lambda i=model_id: self.remove(i), "ghost", width=80, height=32,
                        font_size=12).pack(side="left", padx=(0, 6))
            if chosen:
                make_button(cell, "Используется", None, "ghost", width=120, height=32, font_size=12,
                            state="disabled", text_color_disabled=t.ACCENT).pack(side="left")
            else:
                make_button(cell, "Использовать", lambda i=model_id: self.use(i), "primary", width=120, height=32,
                            font_size=12).pack(side="left")
        else:
            make_button(cell, "Скачать", lambda i=model_id: self.download(i), "primary", width=110, height=32,
                        font_size=12).pack()

    # ---------- Choices ----------

    def choose_engine(self, engine):
        info = self.stt.info()
        if engine == WHISPER and not info["whisper_model"] and info["installed"]:
            # No model is chosen yet: the first of the downloaded ones is taken
            self.stt.set_whisper(info["installed"][0])
        self.stt.set_engine(engine)
        self.refresh()

    def use(self, model_id):
        try:
            self.stt.set_whisper(model_id)
        except SttError as error:
            messagebox.showwarning("Whisper", str(error), parent=self)
            return
        if self.stt.info()["engine"] != WHISPER:
            self.stt.set_engine(WHISPER)
        self.refresh()

    def choose_language(self, name):
        code = next((key for key, title in LANGUAGES if title == name), "ru")
        self.stt.set_whisper(language=code)
        self.refresh()

    def remove(self, model_id):
        if not messagebox.askyesno("Удалить модель", f"Удалить модель «{self.title_of(model_id)}»? Её можно будет "
                                   "скачать снова.", parent=self):
            return
        try:
            self.stt.remove_model(model_id)
        except SttError as error:
            messagebox.showwarning("Whisper", str(error), parent=self)
        self.refresh()

    # ---------- Download ----------

    def download(self, model_id):
        if model_id in self.cancel_events:
            return
        cancel = threading.Event()
        self.cancel_events[model_id] = cancel
        self._refresh_catalog(self.stt.info())
        events, library = self.app.events, self.stt.library
        last = [0.0]

        def progress(fraction):
            if fraction - last[0] >= PROGRESS_STEP or fraction >= 1.0:
                last[0] = fraction
                events.put(("stt_progress", model_id, fraction))

        def work():
            error, cancelled = None, False
            try:
                library.download(model_id, progress=progress, cancel=cancel)
            except SttCancelled:
                cancelled = True
            except SttError as exc:
                error = str(exc)
            except Exception as exc:
                error = f"Не получилось скачать модель: {exc}"
            events.put(("stt_downloaded", model_id, error, cancelled))

        threading.Thread(target=work, daemon=True, name="whisper-download").start()

    # ---------- Events from the threads ----------

    def handle_event(self, kind, *args):
        if kind == "stt_progress":
            model_id, fraction = args
            self.fractions[model_id] = fraction
            bar = self.busy.get(model_id)
            if bar is not None and bar.winfo_exists():
                bar.set(fraction)
        elif kind == "stt_downloaded":
            model_id, error, _cancelled = args
            self.cancel_events.pop(model_id, None)
            self.busy.pop(model_id, None)
            self.fractions.pop(model_id, None)
            if not error:
                self._take_downloaded(model_id)
            self.refresh()
            if error:
                messagebox.showwarning("Модель не скачана", error, parent=self)

    def _take_downloaded(self, model_id):
        """A model that has just been downloaded is put to work if Whisper waits for one."""
        info = self.stt.info()
        if info["engine"] == WHISPER and not info["whisper_model"] and self.stt.library.is_installed(model_id):
            try:
                self.stt.set_whisper(model_id)
            except SttError:
                pass
