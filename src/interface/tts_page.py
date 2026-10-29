"""The page "Озвучка": the voice of the assistant, its speed and volume, the catalog and the own voices."""
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from src.interface import graphics as g
from src.interface import theme as t
from src.interface.pages import scrolling_page
from src.interface.widgets import Card, make_button, make_progress
from src.speech.tts_module import PREVIEW_TEXT
from src.speech.voices import CATALOG, NEURAL, VoiceError

SOURCE_TITLES = {"builtin": "Windows", "catalog": "каталог", "custom": "свой"}
ARCHIVES = [("Голос Piper или архив sherpa-onnx", "*.onnx *.tar.bz2 *.tar.gz *.tgz *.zip"), ("Все файлы", "*.*")]
HELP_TEXT = (
    "Подойдёт голос Piper: файл .onnx и рядом файл .onnx.json (русские голоса лежат на huggingface.co в "
    "rhasspy/piper-voices, папка ru/ru_RU). Можно выбрать и готовый архив или папку модели sherpa-onnx "
    "(vits-piper-…). Голос копируется в папку data/voices, исходные файлы остаются на месте."
)


class TtsPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.app = app
        self.tts = app.assistant.tts
        self.library = self.tts.library
        self.busy = {}           # voice id -> its progress bar while it is being downloaded
        self.cancel_events = {}
        self._commit_job = None
        body, _ = scrolling_page(self, "Озвучка", "Каким голосом говорит ассистент")

        self.voices_card = Card(body, "Голоса", "Нажмите «Слушать», чтобы услышать, и «Выбрать», чтобы говорить им.")
        self.voices_card.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        self.voices_box = ctk.CTkFrame(self.voices_card, fg_color="transparent")
        self.voices_box.pack(fill="x", padx=14, pady=(0, 12))
        self.voices_box.grid_columnconfigure(0, weight=1)
        self.engine_note = self.voices_card.add_text("", color=t.WARN, size=12, pady=(0, 12))

        self._build_settings(body, 2)
        self._build_catalog(body, 3)
        self._build_custom(body, 4)
        self.refresh()
        # The voice can be changed by speaking, so the list is read again whenever the page is opened
        self.bind("<Map>", lambda _e: self.refresh())

    # ---------- Building ----------

    def _build_settings(self, body, row):
        card = Card(body, "Темп и громкость", "Меняются сразу, голосом тоже: «говори быстрее», «говори тише».")
        card.grid(row=row, column=0, sticky="ew", pady=(0, 14))
        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.pack(fill="x", padx=22, pady=(0, 6))
        grid.grid_columnconfigure(1, weight=1)
        settings = self.tts.settings
        self.rate_label = self._slider_row(grid, 0, "Темп речи", settings.RATE_RANGE, settings.rate, self._on_rate)
        self.volume_label = self._slider_row(grid, 1, "Громкость", settings.VOLUME_RANGE, settings.volume,
                                             self._on_volume)
        test = ctk.CTkFrame(card, fg_color="transparent")
        test.pack(fill="x", padx=22, pady=(4, 18))
        test.grid_columnconfigure(0, weight=1)
        self.test_entry = ctk.CTkEntry(
            test, height=38, corner_radius=19, border_color=t.BORDER, fg_color=t.SURFACE_ALT, text_color=t.TEXT,
            placeholder_text="Напишите фразу, чтобы услышать её выбранным голосом", placeholder_text_color=t.MUTED,
            font=(t.FONT, 13),
        )
        self.test_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.test_entry.bind("<Return>", lambda _e: self.say_test())
        make_button(test, "Произнести", self.say_test, "primary", width=120, height=38).grid(row=0, column=1)

    def _slider_row(self, grid, row, caption, bounds, value, command):
        ctk.CTkLabel(grid, text=caption, font=(t.FONT, 13), text_color=t.TEXT, anchor="w", width=110).grid(
            row=row, column=0, sticky="w", pady=6)
        slider = ctk.CTkSlider(
            grid, from_=bounds[0], to=bounds[1], number_of_steps=int(round((bounds[1] - bounds[0]) / 0.05)),
            progress_color=t.ACCENT, button_color=t.ACCENT, button_hover_color=t.ACCENT_HOVER, fg_color=t.BORDER,
            command=command,
        )
        slider.set(value)
        slider.grid(row=row, column=1, sticky="ew", padx=12)
        label = ctk.CTkLabel(grid, text="", font=(t.FONT, 13), text_color=t.MUTED, width=60, anchor="e")
        label.grid(row=row, column=2)
        label.configure(text=self._format(caption, value))
        setattr(self, "rate_slider" if row == 0 else "volume_slider", slider)
        return label

    @staticmethod
    def _format(caption, value):
        return f"×{value:.2f}".rstrip("0").rstrip(".") if caption == "Темп речи" else f"{round(value * 100)}%"

    def _build_catalog(self, body, row):
        self.catalog_card = Card(
            body, "Каталог голосов",
            "Нейросетевые голоса: звучат естественнее системных и работают без интернета. "
            "Скачать нужно один раз.",
        )
        self.catalog_card.grid(row=row, column=0, sticky="ew", pady=(0, 14))
        self.catalog_box = ctk.CTkFrame(self.catalog_card, fg_color="transparent")
        self.catalog_box.pack(fill="x", padx=14, pady=(0, 12))
        self.catalog_box.grid_columnconfigure(0, weight=1)

    def _build_custom(self, body, row):
        card = Card(body, "Добавить свой голос", HELP_TEXT)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 14))
        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.pack(fill="x", padx=22, pady=(0, 6))
        make_button(buttons, "Выбрать файл…", self.add_file, "primary", width=150, height=38).pack(side="left")
        make_button(buttons, "Выбрать папку…", self.add_folder, "secondary", width=150, height=38).pack(
            side="left", padx=8)
        self.import_progress = make_progress(card)
        self.import_progress.set(0)
        self.import_status = card.add_text("", color=t.MUTED, size=12, pady=(8, 16))

    # ---------- The lists ----------

    def refresh(self):
        current = self.tts.current_voice()
        current_id = current.id if current else None
        for child in self.voices_box.winfo_children():
            child.destroy()
        voices = self.tts.voices()
        if not voices:
            ctk.CTkLabel(self.voices_box, text="Голосов нет. Скачайте один из каталога ниже.",
                         font=(t.FONT, 13), text_color=t.MUTED).grid(row=0, column=0, pady=20)
        for index, voice in enumerate(voices):
            self._voice_row(index, voice, voice.id == current_id)
        if self.tts.neural_available():
            self.engine_note.pack_forget()
        else:
            self.engine_note.configure(
                text="Нейроголоса не работают: не установлен sherpa-onnx. Выполните «uv sync» или запустите "
                     "Astra.bat заново.")
            self.engine_note.pack(fill="x", padx=22, pady=(0, 12))
        self._refresh_catalog()

    def _voice_row(self, index, voice, selected):
        frame = ctk.CTkFrame(self.voices_box, corner_radius=16, fg_color=t.SURFACE_ALT,
                             border_width=2 if selected else 1, border_color=t.ACCENT if selected else t.BORDER)
        frame.grid(row=index, column=0, sticky="ew", pady=(0, 8))
        frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(frame, text="", image=g.icon("check" if selected else "speaker", t.ACCENT if selected else
                                                  t.MUTED, 22)).grid(row=0, column=0, rowspan=2, padx=(16, 12), pady=12)
        source = SOURCE_TITLES.get(voice.source, "")
        ctk.CTkLabel(frame, text=voice.name, font=(t.FONT, 14, "bold"), text_color=t.TEXT, anchor="w").grid(
            row=0, column=1, sticky="w", pady=(10, 0))
        details = " · ".join(x for x in (source, voice.description if voice.kind == NEURAL else "") if x)
        ctk.CTkLabel(frame, text=details, font=(t.FONT, 12), text_color=t.MUTED, anchor="w").grid(
            row=1, column=1, sticky="w", pady=(0, 10))
        buttons = ctk.CTkFrame(frame, fg_color="transparent")
        buttons.grid(row=0, column=2, rowspan=2, padx=12)
        if voice.removable:
            make_button(buttons, "Удалить", lambda v=voice: self.remove_voice(v), "ghost", width=80, height=32,
                        font_size=12).pack(side="left", padx=(0, 4))
        make_button(buttons, "Слушать", lambda v=voice: self.tts.preview(v.id), "secondary", width=96, height=32,
                    image=g.icon("play", t.TEXT, 14), font_size=12).pack(side="left", padx=(0, 6))
        if selected:
            make_button(buttons, "Выбран", None, "ghost", width=90, height=32, font_size=12,
                        state="disabled", text_color_disabled=t.ACCENT).pack(side="left")
        else:
            make_button(buttons, "Выбрать", lambda v=voice: self.choose(v), "primary", width=90, height=32,
                        font_size=12).pack(side="left")

    def _refresh_catalog(self):
        for child in self.catalog_box.winfo_children():
            child.destroy()
        for index, entry in enumerate(CATALOG):
            installed = self.library.is_installed(entry.id)
            frame = ctk.CTkFrame(self.catalog_box, corner_radius=16, fg_color=t.SURFACE_ALT, border_width=1,
                                 border_color=t.BORDER)
            frame.grid(row=index, column=0, sticky="ew", pady=(0, 8))
            frame.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(frame, text="", image=g.icon("speaker", t.MUTED, 22)).grid(
                row=0, column=0, rowspan=2, padx=(16, 12), pady=12)
            ctk.CTkLabel(frame, text=entry.name, font=(t.FONT, 14, "bold"), text_color=t.TEXT, anchor="w").grid(
                row=0, column=1, sticky="w", pady=(10, 0))
            ctk.CTkLabel(frame, text=f"{entry.description} · ≈{entry.size_mb} МБ", font=(t.FONT, 12),
                         text_color=t.MUTED, anchor="w").grid(row=1, column=1, sticky="w", pady=(0, 10))
            cell = ctk.CTkFrame(frame, fg_color="transparent")
            cell.grid(row=0, column=2, rowspan=2, padx=12)
            if entry.id in self.cancel_events:
                bar = make_progress(cell, width=130)
                bar.set(0)
                bar.pack(side="left", padx=(0, 8))
                self.busy[entry.id] = bar
                make_button(cell, "Отмена", lambda i=entry.id: self.cancel_events[i].set(), "ghost", width=80,
                            height=32, font_size=12).pack(side="left")
            elif installed:
                make_button(cell, "Установлен", None, "ghost", width=110, height=32, font_size=12,
                            state="disabled", text_color_disabled=t.OK).pack()
            else:
                make_button(cell, "Скачать", lambda e=entry: self.download(e), "primary", width=110, height=32,
                            font_size=12).pack()

    # ---------- Actions ----------

    def choose(self, voice):
        try:
            self.tts.set_voice(voice.id)
        except VoiceError as error:
            messagebox.showerror("Голос", str(error), parent=self)
            return
        self.refresh()
        self.tts.preview(voice.id, "Теперь я говорю этим голосом.")

    def remove_voice(self, voice):
        if not messagebox.askyesno("Удалить голос", f"Удалить голос «{voice.name}»? Его можно будет скачать или "
                                   "добавить снова.", parent=self):
            return
        was_current = self.tts.current_voice() is not None and self.tts.current_voice().id == voice.id
        try:
            self.library.remove(voice.id)
        except VoiceError as error:
            messagebox.showerror("Голос", str(error), parent=self)
            return
        if was_current:
            self.tts.settings.set_voice(None)
        self.refresh()

    def say_test(self):
        text = self.test_entry.get().strip() or PREVIEW_TEXT
        voice = self.tts.current_voice()
        self.tts.preview(voice.id if voice else None, text)

    def _on_rate(self, value):
        self.rate_label.configure(text=self._format("Темп речи", value))
        self._commit_later(lambda: self.tts.set_rate(value))

    def _on_volume(self, value):
        self.volume_label.configure(text=self._format("Громкость", value))
        self._commit_later(lambda: self.tts.set_volume(value))

    # Settings are written to the disk when the slider stops moving
    def _commit_later(self, action):
        if self._commit_job is not None:
            self.after_cancel(self._commit_job)
        self._commit_job = self.after(400, action)

    # ---------- Download and adding ----------

    def download(self, entry):
        if entry.id in self.cancel_events:
            return
        cancel = threading.Event()
        self.cancel_events[entry.id] = cancel
        self._refresh_catalog()

        def work():
            error = None
            try:
                self.library.download(entry.id, progress=lambda f: self.app.events.put(("tts_progress", entry.id, f)),
                                      cancel=cancel)
            except VoiceError as exc:
                error = str(exc)
            except Exception as exc:
                error = f"Не получилось: {exc}"
            self.app.events.put(("tts_downloaded", entry.id, error))

        threading.Thread(target=work, daemon=True, name="voice-download").start()

    def add_file(self):
        path = filedialog.askopenfilename(parent=self, title="Выберите файл голоса (.onnx) или архив", filetypes=ARCHIVES)
        if path:
            self._import(path)

    def add_folder(self):
        path = filedialog.askdirectory(parent=self, title="Выберите папку с моделью голоса")
        if path:
            self._import(path)

    def _import(self, path):
        suggested = Path(path).stem.replace("_", " ").replace("-", " ").replace(".onnx", "")
        dialog = ctk.CTkInputDialog(text="Как назвать этот голос?", title="Название голоса")
        name = (dialog.get_input() or "").strip() or suggested
        self.import_status.configure(text="Добавляю голос… Если не хватает данных произношения, они скачаются.",
                                     text_color=t.MUTED)
        self.import_progress.pack(fill="x", padx=22, before=self.import_status)

        def work():
            voice, error = None, None
            try:
                voice = self.library.import_voice(
                    path, name=name, progress=lambda f: self.app.events.put(("tts_import_progress", f)))
            except VoiceError as exc:
                error = str(exc)
            except Exception as exc:
                error = f"Не получилось добавить голос: {exc}"
            self.app.events.put(("tts_imported", voice.name if voice else None, voice.id if voice else None, error))

        threading.Thread(target=work, daemon=True, name="voice-import").start()

    # ---------- Events from the threads ----------

    def handle_event(self, kind, *args):
        if kind == "tts_progress":
            voice_id, fraction = args
            bar = self.busy.get(voice_id)
            if bar is not None and bar.winfo_exists():
                bar.set(fraction)
        elif kind == "tts_downloaded":
            voice_id, error = args
            self.cancel_events.pop(voice_id, None)
            self.busy.pop(voice_id, None)
            self.refresh()
            if error:
                messagebox.showwarning("Голос не скачан", error, parent=self)
        elif kind == "tts_import_progress":
            self.import_progress.set(args[0])
        elif kind == "tts_imported":
            name, voice_id, error = args
            self.import_progress.pack_forget()
            self.import_progress.set(0)
            if error:
                self.import_status.configure(text=error, text_color=t.ERROR)
                return
            self.import_status.configure(text=f"Голос «{name}» добавлен. Нажмите «Выбрать», чтобы говорить им.",
                                         text_color=t.OK)
            self.refresh()
