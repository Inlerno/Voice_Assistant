import json
import logging
import queue
import threading
import time
from collections import deque
from concurrent.futures import Future
from pathlib import Path

import sounddevice as sd
import vosk

from src.speech.voice_profile import VoiceProfile, sample_features, word_features

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"
# Big Russian models, the best one first. They are not stored in git: scripts/download_vosk_model.py puts one
# of them into src/assets, and it is picked up from there without any settings
BIG_MODEL_NAMES = ("vosk-model-ru-0.42", "vosk-model-ru-0.22")


# The big model that lies in the assets folder, or None when it has not been downloaded
def find_big_model(assets_dir=ASSETS_DIR):
    for name in BIG_MODEL_NAMES:
        path = Path(assets_dir) / name
        # Only unpacked models count: an interrupted unpacking leaves a folder without these parts
        if (path / "am").is_dir() and (path / "conf").is_dir() and (path / "graph").is_dir():
            return path
    return None

# How much audio before the end of a phrase is kept to analyze the wake word
WAKE_BUFFER_SECONDS = 3.0
# The recognizer checks for new audio at this interval, in seconds
WAKE_BLOCK_SECONDS = 0.1
# A sample for the voice profile must be spoken within this time, in seconds
ENROLL_TIMEOUT_SECONDS = 10.0
# Samples needed at least to build a profile
ENROLL_MIN_SAMPLES = 3


# Audio of the phrase that is being recorded and the time Vosk spent on it
class _Utterance:
    def __init__(self, max_blocks, samplerate):
        self.history = deque(maxlen=max_blocks)
        self.decode_ms = 0.0
        self.samplerate = samplerate
        # Everything the recognizer has received so far. Vosk counts the time of words from the very
        # start of the stream, so this counter is never reset together with the phrase
        self.fed_samples = 0

    def reset(self):
        self.history.clear()
        self.decode_ms = 0.0


# Part of the recorded audio where the recognizer heard the words, with a small margin on both sides.
#   audio          - 16-bit mono audio that ends at the moment the recognizer received it last
#   buffer_start_s - stream time of the first sample of audio, in seconds
#   words          - the "result" list of a Vosk result with word times (start and end, in seconds)
# The margin is small on purpose: in a continuous phrase a larger one catches the beginning of the next word
def crop_to_words(audio: bytes, buffer_start_s: float, words: list, samplerate: int, margin_s: float = 0.04) -> bytes:
    if not words:
        return audio
    first = int(round((words[0]["start"] - margin_s - buffer_start_s) * samplerate))
    last = int(round((words[-1]["end"] + margin_s - buffer_start_s) * samplerate))
    first = max(0, first)
    last = min(len(audio) // 2, last)
    # A crop that is almost empty means the times do not belong to this audio, keep everything
    if last - first < int(0.1 * samplerate):
        return audio
    return audio[first * 2:last * 2]


class SpeechRecognizer:
    # model_path is the small model: it starts at once and is the only one that works with the wake word grammar.
    # big_model_path is the big model for commands: "auto" looks for it in the assets folder, None turns it off.
    def __init__(self, model_path="src/assets/vosk-model-small-ru-0.22", input_device=None, profile=None,
                 big_model_path="auto"):
        self.model = vosk.Model(model_path)
        # The model that recognizes commands: the small one until the big one is loaded
        self.command_model = self.model
        self.big_model_path = find_big_model() if big_model_path == "auto" else big_model_path
        self._big_model_started = False
        # What the interface shows about the big model: none (not downloaded), pending, loading, ready or failed
        self.big_model_state = "none" if self.big_model_path is None else "pending"
        # While the microphone is switched off the assistant does not listen for the wake word
        self.paused = False
        self.input_device = input_device or sd.default.device[0]
        self.audio_queue = queue.Queue()
        # What the owner's wake word sounds like, used to tell the owner from everybody else
        self.profile = profile if profile is not None else VoiceProfile.load()
        # Optional callbacks for the interface:
        #   on_state("idle"|"listening"|"processing")
        #   on_phrase(text, stats)   stats: decode_ms - time Vosk worked, audio_s - length of the recording
        #   on_activation(info)      the wake word was heard: text, accepted, score, threshold, decode_ms, dtw_ms
        self.on_state = None
        self.on_phrase = None
        self.on_activation = None
        # on_model(text): the state of the big model, for example that it has been loaded
        self.on_model = None
        # Jobs that need the microphone to themselves, they run on the assistant thread
        self._tasks = queue.Queue()

    def _notify(self, callback, *args):
        if callback:
            try:
                callback(*args)
            except Exception:
                logger.exception("Interface callback error")

    # ---------- Big model ----------

    # Load the big model in the background, the assistant works with the small one meanwhile.
    # Returns the thread, or None when there is no big model or it has been started already.
    # Called after the callbacks are set, so the interface does not miss the messages.
    def start_big_model_loading(self):
        if self.big_model_path is None or self._big_model_started:
            return None
        self._big_model_started = True
        thread = threading.Thread(target=self._load_big_model, daemon=True)
        thread.start()
        return thread

    def _load_big_model(self):
        path = Path(self.big_model_path)
        started = time.perf_counter()
        logger.info("Загрузка большой модели %s", path.name)
        self.big_model_state = "loading"
        self._notify(self.on_model, "Загружаю большую модель распознавания, это может занять до минуты. "
                                    "Пока команды распознаёт маленькая.")
        try:
            model = vosk.Model(str(path))
        except Exception:
            logger.exception("Не удалось загрузить большую модель %s", path)
            self.big_model_state = "failed"
            self._notify(self.on_model, "Большую модель загрузить не удалось (см. журнал), работает маленькая.")
            return
        # One assignment: a command that is being recognized right now keeps the model it started with
        self.command_model = model
        self.big_model_state = "ready"
        seconds = time.perf_counter() - started
        logger.info("Большая модель готова за %.0f с", seconds)
        self._notify(self.on_model, f"Большая модель {path.name} готова ({seconds:.0f} с). Команды теперь "
                                    f"распознаются точнее, но немного дольше.")

    # ---------- Exclusive jobs ----------

    # Run a function on the assistant thread when the microphone is free (for example recording the voice samples).
    # Returns a Future with the result of the function.
    def run_exclusive(self, func) -> Future:
        future = Future()
        self._tasks.put((func, future))
        return future

    def has_pending_tasks(self) -> bool:
        return not self._tasks.empty()

    def _run_pending_tasks(self):
        while True:
            try:
                func, future = self._tasks.get_nowait()
            except queue.Empty:
                return
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(func())
            except Exception as e:
                logger.exception("Ошибка в задаче с микрофоном")
                future.set_exception(e)

    # ---------- Helpers ----------

    def _samplerate(self) -> int:
        return int(sd.query_devices(self.input_device, 'input')['default_samplerate'])

    def _clear_audio(self):
        with self.audio_queue.mutex:
            self.audio_queue.queue.clear()

    def _input_stream(self, samplerate, blocksize):
        def stream_callback(indata, frames, time_info, status):
            if status:
                logger.warning("Audio stream status: %s", status)
            self.audio_queue.put(bytes(indata))

        return sd.RawInputStream(
            samplerate=samplerate,
            blocksize=blocksize,
            device=self.input_device,
            dtype='int16',
            channels=1,
            callback=stream_callback
        )

    # Vosk grammar: only these words (and "unknown") can be recognized, which is much faster and more reliable
    @staticmethod
    def _grammar(words):
        return json.dumps(sorted({w.lower() for w in words}) + ["[unk]"], ensure_ascii=False)

    # Read audio until the recognizer finds the end of a phrase.
    # Returns (text, audio, decode_ms), or None on timeout or when interrupted() becomes true.
    def _read_utterance(self, rec, state, timeout, interrupted=None):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if interrupted is not None and interrupted():
                return None
            try:
                data = self.audio_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            state.history.append(data)
            state.fed_samples += len(data) // 2
            started = time.perf_counter()
            final = rec.AcceptWaveform(data)
            result = json.loads(rec.Result()) if final else None
            state.decode_ms += (time.perf_counter() - started) * 1000.0
            if final:
                audio = b"".join(state.history)
                # Keep only the recognized words: the rest of the phrase is not the wake word
                buffer_start_s = (state.fed_samples - len(audio) // 2) / state.samplerate
                audio = crop_to_words(audio, buffer_start_s, result.get('result', []), state.samplerate)
                decode_ms = state.decode_ms
                state.reset()
                return result.get('text', '').strip(), audio, decode_ms
        return None

    # ---------- Wake word ----------

    # Listen for the activation word
    def listen_for_activation(self, activation_words, callback=None):
        self._notify(self.on_state, "idle")
        while True:
            self._run_pending_tasks()
            if self.paused:
                self._wait_while_paused()
                continue
            if self._wait_for_wake_word(activation_words):
                # The microphone is already closed here, so the answer is not recorded
                if callback:
                    callback()
                return

    # Switch the microphone off or on. The interface button calls it from its own thread.
    def set_paused(self, paused: bool):
        self.paused = bool(paused)
        # The assistant thread may be sitting inside a command session: the state is shown at once
        self._notify(self.on_state, "paused" if self.paused else "idle")

    # The microphone is off: nothing is listened to, only the jobs of the interface (they ask for the microphone
    # themselves) still run
    def _wait_while_paused(self):
        self._notify(self.on_state, "paused")
        logger.info("Микрофон выключен")
        while self.paused and not self.has_pending_tasks():
            time.sleep(0.1)
        if not self.paused:
            logger.info("Микрофон включён")
            self._notify(self.on_state, "idle")

    # Returns True when the wake word of the owner was heard, False when a job needs the microphone
    # or the microphone was switched off
    def _wait_for_wake_word(self, activation_words):
        samplerate = self._samplerate()
        blocksize = int(samplerate * WAKE_BLOCK_SECONDS)
        rec = vosk.KaldiRecognizer(self.model, samplerate, self._grammar(activation_words))
        rec.SetWords(True)
        state = _Utterance(int(WAKE_BUFFER_SECONDS / WAKE_BLOCK_SECONDS) + 1, samplerate)
        # Drop stale audio left from the previous session (for example the assistant's own voice)
        self._clear_audio()

        with self._input_stream(samplerate, blocksize):
            logger.info("Ожидание активационного слова 'Астра'...")
            while True:
                utterance = self._read_utterance(rec, state, 0.5, self._must_stop_listening)
                if utterance is None:
                    if self._must_stop_listening():
                        return False
                    continue
                text, audio, decode_ms = utterance
                if self._check_wake_word(text, audio, samplerate, decode_ms, activation_words):
                    return True

    def _must_stop_listening(self) -> bool:
        return self.paused or self.has_pending_tasks()

    # Decide whether a recognized phrase is the wake word said by the owner
    def _check_wake_word(self, text, audio, samplerate, decode_ms, activation_words) -> bool:
        words = {w.lower() for w in activation_words}
        if not any(token in words for token in text.split()):
            return False

        info = {"text": text, "accepted": True, "decode_ms": decode_ms, "dtw_ms": 0.0,
                "score": None, "threshold": None, "adapted": False}
        profile = self.profile
        if profile.enrolled:
            started = time.perf_counter()
            features = word_features(audio, samplerate)
            score = profile.score(features) if features is not None else None
            info["dtw_ms"] = (time.perf_counter() - started) * 1000.0
            info["score"] = score
            info["threshold"] = profile.active_threshold
            info["accepted"] = score is not None and profile.accepts(score)
            if info["accepted"] and profile.would_adapt(score):
                # Learning from this phrase takes a while, the owner should not wait for it
                info["adapted"] = True
                threading.Thread(target=self._adapt_profile, args=(features, score), daemon=True).start()
        logger.info(
            "Активационное слово: %s, принято: %s, оценка: %s, порог: %s, Vosk: %.0f мс, сравнение: %.0f мс",
            text, info["accepted"], info["score"], info["threshold"], decode_ms, info["dtw_ms"]
        )
        self._notify(self.on_activation, info)
        return info["accepted"]

    # ---------- Voice profile ----------

    def _adapt_profile(self, features, score):
        try:
            if self.profile.adapt(features, score):
                self.profile.save()
        except Exception:
            logger.exception("Не удалось дообучить профиль голоса")

    # Record the owner saying the wake word several times and build the voice profile.
    # Must run with the free microphone, see run_exclusive. Returns a dict with ok and message.
    #   on_progress(done, total, message) is called while the samples are being recorded
    #   stop_event cancels the recording
    def enroll_wake_word(self, word, count=5, on_progress=None, stop_event=None):
        word = word.lower()
        samplerate = self._samplerate()
        blocksize = int(samplerate * WAKE_BLOCK_SECONDS)
        rec = vosk.KaldiRecognizer(self.model, samplerate, self._grammar([word]))
        rec.SetWords(True)
        state = _Utterance(int(WAKE_BUFFER_SECONDS / WAKE_BLOCK_SECONDS) + 1, samplerate)
        stopped = stop_event.is_set if stop_event is not None else (lambda: False)
        templates = []
        recorded = 0
        attempts = 0

        def progress(message):
            self._notify(on_progress, recorded, count, message)

        self._clear_audio()
        progress(f"Скажите «{word.capitalize()}»")
        with self._input_stream(samplerate, blocksize):
            while recorded < count and attempts < count * 3:
                if stopped():
                    break
                attempts += 1
                utterance = self._read_utterance(rec, state, ENROLL_TIMEOUT_SECONDS, stopped)
                if utterance is None:
                    state.reset()
                    if not stopped():
                        progress("Не слышу голос. Говорите громче и ближе к микрофону.")
                    continue
                text, audio, _ = utterance
                if word not in text.split():
                    progress("Не расслышала слово, повторите.")
                    continue
                features = sample_features(audio, samplerate, seed=attempts)
                if features[0] is None:
                    progress("Слово получилось слишком долгим, тихим или нечётким. Повторите.")
                    continue
                templates.extend(features)
                recorded += 1
                progress("Записано. Скажите ещё раз." if recorded < count else "Готово.")
                # Let the end of the word fade out so it is not mistaken for the next sample
                time.sleep(0.6)
                self._clear_audio()
                state.reset()

        if stopped():
            return {"ok": False, "cancelled": True, "message": "Запись отменена."}
        if recorded < ENROLL_MIN_SAMPLES:
            return {
                "ok": False,
                "message": f"Удалось записать только {recorded} образцов из {count}. Попробуйте в тишине и ближе к микрофону.",
            }
        self.profile.enroll(word, templates)
        self.profile.save()
        logger.info("Профиль голоса создан: образцов %s, порог %.3f", recorded, self.profile.threshold)
        return {"ok": True, "recorded": recorded, "threshold": self.profile.threshold,
                "message": f"Готово: записано образцов {recorded}. Ассистент будет реагировать на ваш голос."}

    # ---------- Commands ----------

    # Recognize a voice command.
    # Recording stops as soon as Vosk detects the end of the phrase, so the
    # command is recognized right after the user stops speaking.
    #   max_duration      - hard limit for the whole recording, in seconds
    #   no_speech_timeout - give up if nothing was said within this time, in seconds
    def listen_command(self, max_duration=8, no_speech_timeout=5):
        self._notify(self.on_state, "listening")
        try:
            samplerate = self._samplerate()
            rec = vosk.KaldiRecognizer(self.command_model, samplerate)
            block_seconds = 0.2
            blocksize = int(samplerate * block_seconds)
            command_queue = queue.Queue()

            def stream_callback(indata, frames, time_info, status):
                if status:
                    logger.warning("Audio stream status: %s", status)
                command_queue.put(bytes(indata))

            max_blocks = round(max_duration / block_seconds)
            timeout_blocks = round(no_speech_timeout / block_seconds)
            blocks = 0
            decode_ms = 0.0
            speech_started = False
            result_json = None
            with sd.RawInputStream(
                samplerate=samplerate,
                blocksize=blocksize,
                device=self.input_device,
                dtype='int16',
                channels=1,
                callback=stream_callback
            ):
                while blocks < max_blocks:
                    try:
                        data = command_queue.get(timeout=1)
                    except queue.Empty:
                        logger.warning("Audio stream produced no data")
                        break
                    blocks += 1

                    started = time.perf_counter()
                    final = rec.AcceptWaveform(data)
                    if final:
                        # Vosk detected the end of the phrase: its text is in Result()
                        result_json = rec.Result()
                    elif not speech_started and json.loads(rec.PartialResult()).get('partial'):
                        speech_started = True
                    decode_ms += (time.perf_counter() - started) * 1000.0
                    if final:
                        break
                    if not speech_started and blocks >= timeout_blocks:
                        return None

            if result_json is None:
                # The limit was reached in the middle of a phrase: FinalResult returns what was heard
                started = time.perf_counter()
                result_json = rec.FinalResult()
                decode_ms += (time.perf_counter() - started) * 1000.0
            query = json.loads(result_json).get('text', '').lower().strip()
            if query:
                stats = {"decode_ms": decode_ms, "audio_s": blocks * block_seconds}
                logger.info("Recognized: %s (Vosk %.0f мс, запись %.1f с)", query, decode_ms, stats["audio_s"])
                self._notify(self.on_phrase, query, stats)
                self._notify(self.on_state, "processing")
                return query
            return None
        except Exception as e:
            logger.error("Audio recording error: %s", e)
            return None
