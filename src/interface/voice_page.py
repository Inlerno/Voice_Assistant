"""The "Голос" page: the microphone, its check and the voice profile of the owner."""
import logging
import threading

import customtkinter as ctk
import sounddevice as sd

from src.audio.mic_test import MicCheckResult, check_microphone
from src.interface import theme as t
from src.interface.widgets import Card, make_button, make_progress, page_header
from src.speech.voice_profile import SENSITIVITY_MAX, SENSITIVITY_MIN

MIC_TEST_SECONDS = 5
# How many times the owner says the wake word to create the voice profile
ENROLL_SAMPLES = 5


def input_devices():
    """The audio input devices as "index: name" lines."""
    try:
        devices = sd.query_devices()
    except Exception:
        devices = []
    found = [f"{i}: {d['name']}" for i, d in enumerate(devices) if d["max_input_channels"] > 0]
    return found or ["Нет доступных устройств ввода"]


def device_by_index(index):
    try:
        devices = sd.query_devices()
        if index is not None and 0 <= index < len(devices):
            return f"{index}: {devices[index]['name']}"
    except Exception:
        pass
    return None


def default_device():
    try:
        return device_by_index(sd.default.device[0]) or "Выберите устройство ввода"
    except Exception:
        return "Выберите устройство ввода"


def short_device_name(device):
    if device and ":" in device:
        device = device.split(":", 1)[1].strip()
    return device or "не выбран"


class VoicePage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.app = app
        self.assistant = app.assistant
        self.mic_test_running = False
        self.mic_test_stop = threading.Event()
        self.meter_value = 0.0
        self.enrolling = False
        self.enroll_stop = threading.Event()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        # The cards do not fit into a small window, so they live in a scrollable area
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        scroll.grid(row=0, column=0, sticky="nsew", padx=24)
        scroll.grid_columnconfigure(0, weight=1)
        page_header(scroll, "Голос", "Микрофон и голос владельца").grid(row=0, column=0, sticky="ew", pady=(18, 14))

        self._build_device_card(scroll, 1)
        self._build_test_card(scroll, 2)
        self._build_profile_card(scroll, 3)

    # ---------- Microphone ----------

    def _build_device_card(self, parent, row):
        card = Card(parent, "Микрофон", "Устройство, с которого ассистент слушает команды. Применяется сразу после выбора.")
        card.grid(row=row, column=0, sticky="ew", pady=(0, 14))
        line = ctk.CTkFrame(card, fg_color="transparent")
        line.pack(fill="x", padx=22, pady=(0, 6))
        self.device_options = input_devices()
        self.device_box = ctk.CTkComboBox(
            line, values=self.device_options, font=(t.FONT, 13), dropdown_font=(t.FONT, 13), height=38,
            corner_radius=19, border_color=t.BORDER, fg_color=t.SURFACE_ALT, button_color=t.ACCENT,
            button_hover_color=t.ACCENT_HOVER, text_color=t.TEXT, command=self.on_device_selected,
        )
        saved = device_by_index(getattr(self.assistant, "input_device", None))
        self.device_box.set(saved or default_device())
        self.device_box.pack(side="left", fill="x", expand=True)
        make_button(line, "Обновить", self.refresh_devices, width=100, height=38).pack(side="left", padx=(10, 0))
        self.device_feedback = ctk.CTkLabel(card, text="", font=(t.FONT, 12), text_color=t.MUTED, anchor="w")
        self.device_feedback.pack(fill="x", padx=22, pady=(0, 14))

    def refresh_devices(self):
        self.device_options = input_devices()
        self.device_box.configure(values=self.device_options)
        self._feedback(f"Найдено устройств: {len(self.device_options)}")

    def selected_device_index(self):
        try:
            return int(self.device_box.get().split(":")[0])
        except ValueError:
            return getattr(self.assistant, "input_device", None)

    def on_device_selected(self, selected):
        try:
            index = int(selected.split(":")[0])
        except ValueError:
            self._feedback("Устройство не выбрано")
            return
        self.assistant.set_input_device(index)
        # The previous verdict belongs to another device
        if not self.mic_test_running:
            self.meter_value = 0.0
            self.meter.set(0)
            self.verdict.configure(text="")
            self.recognized.configure(text="")
        self.app.update_mic_label(selected)
        self._feedback("Микрофон применён")
        logging.info("Выбрано устройство: %s", selected)

    def _feedback(self, text):
        self.device_feedback.configure(text=text)
        self.after(3000, lambda: self.device_feedback.configure(text=""))

    # ---------- Microphone check ----------

    def _build_test_card(self, parent, row):
        card = Card(
            parent, "Проверка микрофона",
            "Нажмите «Проверить» и скажите несколько слов, например «раз, два, три». Тест длится "
            f"{MIC_TEST_SECONDS} секунд: покажет уровень сигнала и то, как ассистент вас распознаёт.",
        )
        card.grid(row=row, column=0, sticky="ew", pady=(0, 14))
        line = ctk.CTkFrame(card, fg_color="transparent")
        line.pack(fill="x", padx=22, pady=(0, 6))
        self.meter = make_progress(line)
        self.meter.set(0)
        self.meter.pack(side="left", fill="x", expand=True)
        self.test_button = make_button(line, "Проверить", self.toggle_mic_test, width=120)
        self.test_button.pack(side="left", padx=(14, 0))
        self.verdict = card.add_text("", size=13, bold=True, pady=(6, 0))
        self.recognized = card.add_text("", color=t.MUTED, size=12, pady=(2, 16))

    def toggle_mic_test(self):
        if self.mic_test_running:
            self.mic_test_stop.set()
            return
        device = self.selected_device_index()
        recognizer = getattr(self.assistant, "speech_recognizer", None)
        # The model that recognizes commands, so the test shows what the assistant will really understand
        model = getattr(recognizer, "command_model", None) or getattr(recognizer, "model", None)
        self.mic_test_stop = threading.Event()
        stop_event = self.mic_test_stop
        self.mic_test_running = True
        self.meter_value = 0.0
        self.meter.set(0)
        self.test_button.configure(text="Остановить")
        self.verdict.configure(text="Ждём, пока ассистент освободит микрофон…", text_color=t.MUTED)
        self.recognized.configure(text="")
        events = self.app.events

        # The assistant itself listens to the microphone, so the check runs on its thread when the microphone is free
        def job():
            events.put(("mic_started",))
            return check_microphone(
                device, duration=MIC_TEST_SECONDS, model=model,
                on_level=lambda level: events.put(("mic_level", level)), stop_event=stop_event,
            )

        def done(future):
            try:
                result = future.result()
            except Exception as e:
                result = MicCheckResult("error", f"Не удалось проверить микрофон: {e}")
            events.put(("mic_result", result))

        self.assistant.run_with_microphone(job).add_done_callback(done)

    def set_meter(self, level):
        # A fast rise and a slow fall
        self.meter_value = max(level, self.meter_value * 0.75)
        self.meter.set(self.meter_value)

    def finish_mic_test(self, result):
        self.mic_test_running = False
        self.test_button.configure(text="Проверить")
        self.verdict.configure(text=result.message, text_color=t.MIC_STATUS_COLORS.get(result.status, t.TEXT))
        if result.status in ("ok", "quiet", "loud"):
            if result.text:
                self.recognized.configure(text=f"Распознано: «{result.text}»")
            else:
                self.recognized.configure(text="Речь не распознана: скажите несколько слов во время теста.")
        else:
            self.recognized.configure(text="")
        if result.status in ("error", "cancelled", "silent"):
            self.meter_value = 0.0
            self.meter.set(0)

    # ---------- Voice profile ----------

    def profile(self):
        return getattr(self.assistant, "voice_profile", None)

    def _build_profile_card(self, parent, row):
        word = self.app.activation_word
        card = Card(
            parent, "Голос владельца",
            f"Скажите «{word}» {ENROLL_SAMPLES} раз: ассистент запомнит, как звучит ваш голос, и будет реагировать "
            "на него. По ходу работы профиль сам подстраивается под вас. Он хранится только на этом компьютере.",
        )
        card.grid(row=row, column=0, sticky="ew", pady=(0, 14))
        line = ctk.CTkFrame(card, fg_color="transparent")
        line.pack(fill="x", padx=22, pady=(0, 6))
        self.enroll_bar = make_progress(line)
        self.enroll_bar.set(0)
        self.enroll_bar.pack(side="left", fill="x", expand=True)
        self.enroll_button = make_button(line, "Обучить", self.toggle_enrollment, width=120)
        self.enroll_button.pack(side="left", padx=(14, 0))
        self.voice_status = card.add_text("", size=13, bold=True, pady=(6, 0))
        self.voice_hint = card.add_text("", color=t.MUTED, size=12, pady=(2, 8))
        row_line = ctk.CTkFrame(card, fg_color="transparent")
        row_line.pack(fill="x", padx=22, pady=(0, 4))
        ctk.CTkLabel(row_line, text="Строже", font=(t.FONT, 12), text_color=t.MUTED).pack(side="left")
        self.slider = ctk.CTkSlider(
            row_line, from_=SENSITIVITY_MIN, to=SENSITIVITY_MAX, number_of_steps=16, progress_color=t.ACCENT,
            button_color=t.ACCENT, button_hover_color=t.ACCENT_HOVER, fg_color=t.BORDER,
            command=self.on_sensitivity_changed,
        )
        self.slider.pack(side="left", fill="x", expand=True, padx=12)
        # Settings are written to the disk once, when the slider is released
        self.slider.bind("<ButtonRelease-1>", self.on_sensitivity_released)
        ctk.CTkLabel(row_line, text="Мягче", font=(t.FONT, 12), text_color=t.MUTED).pack(side="left")
        self.reset_button = make_button(
            card, "Сбросить профиль", self.reset_profile, "secondary", width=160, height=34
        )
        self.reset_button.pack(anchor="w", padx=22, pady=(10, 18))
        self.refresh_profile()

    def refresh_profile(self):
        profile = self.profile()
        enrolled = bool(profile and profile.enrolled)
        self.enroll_bar.set(0)
        self.enroll_button.configure(text="Переобучить" if enrolled else "Обучить")
        state = "normal" if enrolled else "disabled"
        # A disabled slider looks just like a working one, so it is grayed out by hand
        paint = t.ACCENT if enrolled else t.OFF
        self.slider.configure(
            state=state, progress_color=paint, button_color=paint,
            button_hover_color=t.ACCENT_HOVER if enrolled else t.OFF,
        )
        self.reset_button.configure(state=state)
        if profile is None:
            self.voice_status.configure(text="Профиль голоса недоступен.", text_color=t.MUTED)
            self.voice_hint.configure(text="")
        elif enrolled:
            self.slider.set(profile.sensitivity)
            self.voice_status.configure(text=profile.summary(), text_color=t.TEXT)
            self._sensitivity_hint(profile.sensitivity)
        else:
            self.voice_status.configure(text=profile.summary(), text_color=t.TEXT)
            self.voice_hint.configure(text="Нажмите «Обучить» и произнесите слово несколько раз.")

    def _sensitivity_hint(self, value):
        self.voice_hint.configure(text=f"Строгость проверки ×{value:.2f}: меньше — строже, больше — мягче.")

    def on_sensitivity_changed(self, value):
        profile = self.profile()
        if profile is not None and profile.enrolled:
            profile.set_sensitivity(value)
            self._sensitivity_hint(profile.sensitivity)

    def on_sensitivity_released(self, _event=None):
        profile = self.profile()
        if profile is None or not profile.enrolled:
            return
        try:
            profile.save()
        except OSError:
            logging.exception("Не удалось сохранить профиль голоса")

    def toggle_enrollment(self):
        if self.enrolling:
            self.enroll_stop.set()
            self.enroll_button.configure(state="disabled")
            return
        self.enrolling = True
        self.enroll_stop = threading.Event()
        self.enroll_bar.set(0)
        self.enroll_button.configure(text="Отмена")
        self.reset_button.configure(state="disabled")
        self.voice_status.configure(text="Ждём, пока ассистент освободит микрофон…", text_color=t.MUTED)
        self.voice_hint.configure(text="")
        events = self.app.events
        future = self.assistant.start_voice_enrollment(
            lambda done, total, message: events.put(("enroll_progress", done, total, message)),
            self.enroll_stop, ENROLL_SAMPLES,
        )
        future.add_done_callback(lambda f: events.put(("enroll_done", f)))

    def show_enroll_progress(self, done, total, message):
        self.enroll_bar.set(done / total if total else 0)
        self.voice_status.configure(text=f"Записано {done} из {total}", text_color=t.TEXT)
        self.voice_hint.configure(text=message)

    def finish_enrollment(self, future):
        self.enrolling = False
        self.enroll_button.configure(state="normal")
        try:
            result = future.result()
        except Exception as e:
            logging.exception("Ошибка при записи образцов голоса")
            result = {"ok": False, "message": f"Не удалось записать образцы: {e}"}
        self.refresh_profile()
        status = "ok" if result.get("ok") else ("cancelled" if result.get("cancelled") else "error")
        self.voice_status.configure(text=result.get("message", ""), text_color=t.MIC_STATUS_COLORS[status])

    def reset_profile(self):
        profile = self.profile()
        if profile is not None:
            profile.reset()
        self.refresh_profile()
        self.voice_status.configure(text="Профиль удалён. Ассистент снова реагирует на любой голос.", text_color=t.MUTED)

    # ---------- Events from the assistant thread ----------

    def handle_event(self, kind, *args):
        if kind == "mic_started":
            self.verdict.configure(text="Говорите…", text_color=t.TEXT)
        elif kind == "mic_level":
            self.set_meter(args[0])
        elif kind == "mic_result":
            self.finish_mic_test(args[0])
        elif kind == "enroll_progress":
            self.show_enroll_progress(*args)
        elif kind == "enroll_done":
            self.finish_enrollment(args[0])
