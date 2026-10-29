import json
import logging
import os
import re
import threading
import time
from pathlib import Path

import pygame
import sounddevice as sd
from rapidfuzz import fuzz

from src.integrations.chat_gpt import ChatGPT
from src.interface.interface import VoiceAssistantApp
from src.network import NetworkActions
from src.productivity.apps import AppLauncher
from src.productivity.planner import ALARM, KIND_TITLES, TIMER, Planner
from src.productivity.skills import Productivity
from src.productivity import ru_parse
from src.audio.sound_module import SoundPlayer
from src.speech.speech_module import SpeechRecognizer
from src.system.system_control import SystemControl
from src.speech.tts_module import TTS
from src.speech import voice_commands

logger = logging.getLogger(__name__)


class Assistant:
    def __init__(self):
        self.settings_file = 'src/config/settings.json'
        self.input_device = None
        self.load_settings()

        with open('src/config/commands.json', 'r', encoding='utf-8') as f:
            commands_data = json.load(f)
            self.commands = commands_data['commands']
            self.activation_words = commands_data['activation']

        self.tts = TTS()
        self.speech_recognizer = SpeechRecognizer(input_device=self.input_device)
        self.sound_player = SoundPlayer()
        self.network_actions = NetworkActions()
        self.system_control = SystemControl(self.tts.speak, self.speech_recognizer.listen_command)
        self.chat_gpt = ChatGPT()
        # Timers, alarms, reminders, tasks and programs: the planner works in its own thread
        self.planner = Planner(on_fire=self.on_planner_fire, on_change=self._planner_changed)
        self.apps = AppLauncher()
        self.productivity = Productivity(self.planner, self.apps)

        self.sound_player.play_sound('start')
        self.session_timeout = 15
        # The moment the last phrase was recognized or typed: the time to the first word of the answer counts from it
        self._answer_started = None

        self.interface_manager = VoiceAssistantApp(self)
        self.connect_interface()
        self.planner.start()
        threading.Thread(target=self.main, daemon=True).start()
        self.interface_manager.start_interface()

    # Show the assistant state, recognized phrases and spoken answers in the interface
    def connect_interface(self):
        ui = self.interface_manager

        def on_phrase(text, stats):
            self._answer_started = time.perf_counter()
            ui.add_message(
                "user", text, f"распознавание {stats['decode_ms']:.0f} мс · запись {stats['audio_s']:.1f} с"
            )

        def on_speak(text):
            started, self._answer_started = self._answer_started, None
            meta = f"ответ через {(time.perf_counter() - started) * 1000:.0f} мс" if started else None
            ui.add_message("assistant", text, meta)
            ui.set_state("speaking")

        def on_state(state):
            ui.set_state(state)
            # The speech recognition light in the status bar follows what the microphone is doing
            if state == "paused":
                ui.set_service("stt", "off")
            elif state in ("listening", "processing"):
                ui.set_service("stt", "busy")
            else:
                ui.set_service("stt", "ok")

        self.tts.on_speak = on_speak
        self.tts.on_status = lambda state: ui.set_service("tts", state)
        self.chat_gpt.on_status = lambda state: ui.set_service("ai", state)
        self.speech_recognizer.on_state = on_state
        self.speech_recognizer.on_phrase = on_phrase
        self.speech_recognizer.on_activation = lambda info: ui.add_message("system", self.describe_activation(info))
        self.speech_recognizer.on_model = lambda text: (ui.add_message("system", text), ui.refresh_models())
        ui.set_service("stt", "ok")
        ui.set_service("tts", "ok")
        # The big model (if it was downloaded) loads in the background while the assistant already works
        self.speech_recognizer.start_big_model_loading()

    # A command typed in the interface instead of being said. It runs on the assistant thread,
    # when the microphone is free, exactly like a spoken command. Returns a Future, or None for an empty text.
    def handle_text(self, text):
        original = (text or "").strip()
        # Spoken commands arrive in lower case without punctuation, so the typed ones are brought to the same form
        query = re.sub(r"\s+", " ", re.sub(r"[^\w\s:]|(?<!\d):|:(?!\d)", " ", original.lower())).strip()
        if not query:
            return None
        ui = self.interface_manager
        ui.add_message("user", original, "введено текстом")
        ui.set_state("processing")
        self._answer_started = time.perf_counter()
        return self.speech_recognizer.run_exclusive(lambda: self._run_typed(query))

    def _run_typed(self, query):
        try:
            self.process_command(query)
        except Exception:
            logger.exception("Ошибка при обработке введённой команды: %s", query)
        finally:
            self.interface_manager.set_state("paused" if self.speech_recognizer.paused else "idle")

    # ---------- Timers, alarms, reminders ----------

    def _planner_changed(self):
        ui = getattr(self, "interface_manager", None)
        if ui is not None:
            ui.planner_changed()

    # Something came due: it is said on the assistant thread (the speech engine belongs to it) and shown in a window
    def on_planner_fire(self, item):
        message = self.describe_fired(item)
        logger.info("Сработало: %s", message)
        self.interface_manager.notify(item, message)

        def announce():
            try:
                self.sound_player.play_sound('start')
                for _ in range(2 if item["kind"] == ALARM else 1):
                    self.tts.speak(message)
            finally:
                self.interface_manager.set_state("paused" if self.speech_recognizer.paused else "idle")

        self.speech_recognizer.run_exclusive(announce)

    @staticmethod
    def describe_fired(item):
        text = item.get("text") or ""
        if item["kind"] == TIMER:
            length = ru_parse.describe_duration(item["length"], accusative=True) if item.get("length") else ""
            base = f"Таймер на {length} сработал" if length else "Таймер сработал"
            return f"{base}: {text}." if text else f"{base}."
        if item["kind"] == ALARM:
            clock = item["due"][11:16]
            return f"Будильник! Уже {clock}." + (f" {text}." if text else "")
        prefix = "Напоминаю, пропущено" if item.get("missed") else "Напоминаю"
        return f"{prefix}: {text}." if text else f"{KIND_TITLES[item['kind']]}."

    # What the "Планы" page does with a phrase typed into its field: the same commands as by voice
    def quick_add(self, text):
        query = re.sub(r"\s+", " ", re.sub(r"[^\w\s:]|(?<!\d):|:(?!\d)", " ", (text or "").lower())).strip()
        if not query:
            return None
        try:
            return self.productivity.handle(query)
        except Exception:
            logger.exception("Ошибка при разборе фразы: %s", query)
            return None

    # Switch the microphone off or on: while it is off the assistant does not listen for the wake word
    def set_listening(self, enabled):
        self.speech_recognizer.set_paused(not enabled)

    @property
    def listening(self):
        return not self.speech_recognizer.paused

    # What the "Model" page of the interface shows
    def models_info(self):
        recognizer = self.speech_recognizer
        return {
            "small_model": "vosk-model-small-ru-0.22",
            "big_model": Path(recognizer.big_model_path).name if recognizer.big_model_path else None,
            "big_model_state": recognizer.big_model_state,
            "llm": self.chat_gpt.model_name,
        }

    # One line for the dialog: what was heard as the wake word, whether the voice matched and how long it took
    @staticmethod
    def describe_activation(info):
        word = info["text"]
        timing = f"Vosk {info['decode_ms']:.0f} мс"
        score, threshold = info["score"], info["threshold"]
        if threshold is None:
            return f"Слово «{word}» · профиль голоса не создан · {timing}"
        timing += f" + сравнение {info['dtw_ms']:.0f} мс"
        if score is None or score == float("inf"):
            return f"Слово «{word}» услышано, но не похоже на ваше слово · {timing}"
        comparison = f"{score:.2f} при пороге {threshold:.2f}"
        if not info["accepted"]:
            return f"Слово «{word}» услышано, но голос не совпал: {comparison} · {timing}"
        suffix = " · профиль подстроен" if info["adapted"] else ""
        return f"Слово «{word}» · голос совпал: {comparison} · {timing}{suffix}"

    # ---------- Voice profile and the microphone for the interface ----------

    @property
    def voice_profile(self):
        return self.speech_recognizer.profile

    # Record the owner saying the wake word several times; runs on the assistant thread when the microphone is free.
    # Returns a Future with the result of SpeechRecognizer.enroll_wake_word
    def start_voice_enrollment(self, on_progress, stop_event, count=5):
        word = self.activation_words[0]
        return self.speech_recognizer.run_exclusive(
            lambda: self.speech_recognizer.enroll_wake_word(word, count, on_progress, stop_event)
        )

    # Run a job that needs the microphone to itself (for example the microphone check); returns a Future
    def run_with_microphone(self, func):
        return self.speech_recognizer.run_exclusive(func)

    # Set the input device and save the settings
    def set_input_device(self, device_index):
        self.input_device = device_index
        self.speech_recognizer.input_device = device_index
        self.save_settings()
        logging.info("Устройство ввода обновлено на %s", device_index)

    # Set the interface theme: Dark, Light or System
    def set_appearance(self, mode):
        self.appearance = mode
        self.save_settings()

    def save_settings(self):
        with open(self.settings_file, 'w', encoding='utf-8') as f:
            json.dump({'input_device': self.input_device, 'appearance': self.appearance}, f)

    def load_settings(self):
        self.appearance = 'Dark'
        try:
            with open(self.settings_file, 'r', encoding='utf-8') as f:
                settings = json.load(f)
                self.input_device = settings.get('input_device', sd.default.device[0])
                appearance = settings.get('appearance', 'Dark')
                if appearance in ('Dark', 'Light', 'System'):
                    self.appearance = appearance
        except Exception:
            self.input_device = sd.default.device[0]

    # Open the specified file
    def start_file(self, file_path):
        project_root = Path(__file__).resolve().parents[1]

        path = Path(file_path)
        if not path.is_absolute():
            path = project_root / path

        if path.is_file():
            try:
                logger.info("Попытка открыть файл: %s", path)
                os.startfile(str(path))
                logger.info("Файл успешно открыт: %s", path)
            except Exception as e:
                error_msg = f"Ошибка при открытии файла: {e}"
                logger.error(error_msg, exc_info=True)
                self.tts.speak(error_msg)
        else:
            error_msg = f"Файл не найден: {path}"
            logger.warning(error_msg)
            self.tts.speak(error_msg)

    # Stop the assistant
    def finish(self):
        self.sound_player.play_sound('shutdown')
        pygame.time.wait(3000)
        logger.info("Assistant finished")
        exit()

    def music_player(self):
        self.tts.speak("Что будем слушать?")
        query = self.speech_recognizer.listen_command()
        if query:
            result = self.network_actions.music_player.play_music_request(query)
            self.tts.speak(result)
        else:
            self.tts.speak("Не удалось распознать запрос.")

    def play_film(self):
        self.tts.speak("Что будем смотреть?")
        query = self.speech_recognizer.listen_command()
        if query:
            result = self.network_actions.film_player.play_film_request(query)
            self.tts.speak(result)
        else:
            self.tts.speak("Не удалось распознать запрос.")

    # Get and announce the latest news
    def read_news(self):
        try:
            news_items = self.network_actions.get_latest_news()
            if not news_items:
                self.tts.speak("Не удалось получить новости. Попробуйте позже.")
                return

            self.tts.speak("Вот последние новости:")
            for idx, item in enumerate(news_items, 1):
                self.tts.speak(f"Новость {idx}. {item['title']}")
                time.sleep(0.3)
                if item.get('summary'):
                    self.tts.speak(item['summary'])
                time.sleep(0.5)
        except Exception as e:
            logger.error("Error in news handling: %s", e)

    # Get the weather for the specified city
    def get_city_weather(self):
        self.tts.speak("Укажите город:")
        city = self.speech_recognizer.listen_command()
        if city:
            result = self.network_actions.get_weather(city)
            self.tts.speak(result)
        else:
            self.tts.speak("Не удалось распознать город.")

    # Search the web
    def search_online(self):
        self.network_actions.web_search_service.search_online(self.tts, self.speech_recognizer)

    # Process the user's command
    def process_command(self, query):
        # Timers, alarms, reminders and tasks have keywords of their own, so they are checked first
        try:
            answer = self.productivity.handle(query)
        except Exception:
            logger.exception("Ошибка в команде дел: %s", query)
            answer = None
        if answer:
            self.tts.speak(answer)
            return
        # The voice of the assistant: "смени голос", "говори голосом Денис", "говори тише"
        try:
            answer = voice_commands.handle(self.tts, query)
        except Exception:
            logger.exception("Ошибка в команде голоса: %s", query)
            answer = None
        if answer:
            self.tts.speak(answer)
            return
        best_match, best_score = None, 0
        for cmd in self.commands:
            for trigger in cmd['triggers']:
                score = fuzz.ratio(query, trigger)
                if score > best_score:
                    best_match, best_score = cmd, score
        if best_match and best_score > 75:
            try:
                handler_map = {
                    "self": self,
                    "system_control": self.system_control,
                    "network_actions": self.network_actions
                }
                handler = handler_map.get(best_match.get('handler', 'self'))
                method = getattr(handler, best_match['make'], None)
                if method:
                    if best_match['say']:
                        self.tts.speak(best_match['say'])
                    if best_match['parameters']:
                        if best_match['make'] == "hotkey":
                            method(best_match)
                        else:
                            method(*best_match['parameters'])
                    else:
                        method()
                    return
            except Exception as e:
                self.tts.speak(f"Команда '{query}' пока не реализована.")
                logger.error("Command execution error: %s", e)
            return
        # "Открой калькулятор": a program from the list of applications
        try:
            answer = self.productivity.open_app(query)
        except Exception:
            logger.exception("Ошибка при запуске приложения: %s", query)
            answer = None
        if answer:
            self.tts.speak(answer)
            return
        response = self.chat_gpt.ask(query)
        self.tts.speak(response)

    # Run the assistant's main loop
    def main(self):
        while True:
            try:
                self.speech_recognizer.listen_for_activation(
                    self.activation_words,
                    lambda: self.tts.speak("Слушаю")
                )
                last_active_time = time.time()
                timed_out = True
                while time.time() - last_active_time < self.session_timeout:
                    if self.speech_recognizer.has_pending_tasks() or self.speech_recognizer.paused:
                        # The interface needs the microphone (voice samples, microphone check, a typed command)
                        # or the microphone was switched off
                        timed_out = False
                        break
                    query = self.speech_recognizer.listen_command()
                    if query:
                        try:
                            self.process_command(query)
                        except Exception:
                            # A failed command must not end the session or the assistant
                            logger.exception("Ошибка при обработке команды: %s", query)
                        last_active_time = time.time()
                if timed_out:
                    self.sound_player.play_sound('shutdown')
            except Exception:
                # Microphone or audio stream failure: log it and try again after a pause
                logger.exception("Ошибка в основном цикле, повторная попытка через 2 секунды")
                self.interface_manager.set_service("stt", "error")
                time.sleep(2)
