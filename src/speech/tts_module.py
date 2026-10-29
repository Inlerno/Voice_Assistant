import logging
import queue
import re
import threading

from src.speech.voices import NEURAL, SYSTEM, SYSTEM_PREFIX, TtsSettings, Voice, VoiceError, VoiceLibrary

try:
    import pyttsx3
except Exception:  # not installed or no speech engine on this system
    pyttsx3 = None

logger = logging.getLogger(__name__)

BASE_WORDS_PER_MINUTE = 200
PREVIEW_TEXT = "Здравствуйте! Я Астра, ваш голосовой ассистент. Так звучит этот голос."
_SENTENCE_END = re.compile(r"(?<=[.!?…;])\s+|\n+")
# what a voice cannot read: markdown signs and pictures that a language model likes to add
_NOISE = re.compile(r"[*_`#>~|\\]|[\U0001F000-\U0001FAFF☀-➿]")


def clean_text(text):
    text = _NOISE.sub("", str(text))
    return re.sub(r"[ \t]+", " ", text).strip()


def split_sentences(text):
    parts = [p.strip() for p in _SENTENCE_END.split(text) if p and p.strip()]
    # very short pieces ("Да.") are glued to the next one, so the speech does not stutter
    result = []
    for part in parts:
        if result and len(result[-1]) < 12:
            result[-1] += " " + part
        else:
            result.append(part)
    return result


class _Stopped(Exception):
    """The speech was interrupted."""


class SystemEngine:
    """The voices of Windows (SAPI) through pyttsx3."""

    def __init__(self):
        self.engine = pyttsx3.init()
        self._voices = []
        try:
            for v in self.engine.getProperty("voices"):
                languages = ",".join(str(x) for x in (getattr(v, "languages", None) or []))
                self._voices.append(Voice(
                    id=SYSTEM_PREFIX + str(v.id), name=self._nice_name(v.name), kind=SYSTEM,
                    description="Голос Windows" + (f" ({languages})" if languages else ""),
                ))
        except Exception:
            logger.exception("Не удалось получить список голосов Windows")
        self.default_id = None
        try:
            self.default_id = SYSTEM_PREFIX + str(self.engine.getProperty("voice"))
        except Exception:
            pass

    @staticmethod
    def _nice_name(name):
        # "Microsoft Irina Desktop - Russian" -> "Irina (Russian)"
        name = re.sub(r"^Microsoft\s+", "", str(name)).replace(" Desktop", "")
        return re.sub(r"\s+-\s+(.*)$", r" (\1)", name)

    def voices(self):
        return list(self._voices)

    def say(self, text, voice_id, rate, volume, speaker=0):
        if voice_id and voice_id.startswith(SYSTEM_PREFIX):
            try:
                self.engine.setProperty("voice", voice_id[len(SYSTEM_PREFIX):])
            except Exception:
                logger.warning("Голос Windows не найден: %s", voice_id)
        self.engine.setProperty("rate", int(BASE_WORDS_PER_MINUTE * rate))
        self.engine.setProperty("volume", volume)
        self.engine.say(text)
        self.engine.runAndWait()

    def stop(self):
        try:
            self.engine.stop()
        except Exception:
            pass


class NeuralEngine:
    """Piper voices through sherpa-onnx; the sound goes to the default output device."""

    def __init__(self, library):
        self.library = library
        self._loaded_id = None
        self._tts = None
        self._stop = threading.Event()

    def available(self):
        try:
            import sherpa_onnx  # noqa: F401
            import sounddevice  # noqa: F401
        except Exception:
            return False
        return True

    def _load(self, voice):
        if self._loaded_id == voice.id and self._tts is not None:
            return self._tts
        import sherpa_onnx
        if not self.library.has_shared_data():
            raise VoiceError("Нет данных произношения. Добавьте голос заново (понадобится интернет).")
        config = sherpa_onnx.OfflineTtsConfig(model=sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                model=str(voice.path / "model.onnx"), tokens=str(voice.path / "tokens.txt"),
                data_dir=str(self.library.shared_data)),
            num_threads=2, provider="cpu"))
        self._tts = sherpa_onnx.OfflineTts(config)
        self._loaded_id = voice.id
        return self._tts

    def say(self, text, voice, rate, volume, speaker=0):
        import numpy as np
        import sounddevice as sd

        self._stop.clear()
        tts = self._load(voice)
        sid = speaker if 0 <= speaker < voice.speakers else 0
        sentences = split_sentences(text) or [text]
        ready = queue.Queue(maxsize=2)

        # The next sentence is made while the previous one is played
        def produce():
            try:
                for sentence in sentences:
                    if self._stop.is_set():
                        break
                    audio = tts.generate(sentence, sid=sid, speed=rate)
                    ready.put((np.asarray(audio.samples, dtype="float32"), audio.sample_rate))
            except Exception as error:  # passed to the playing side
                ready.put(error)
            ready.put(None)

        producer = threading.Thread(target=produce, daemon=True, name="tts-synthesis")
        producer.start()
        try:
            while True:
                item = ready.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                samples, rate_hz = item
                if self._stop.is_set():
                    raise _Stopped()
                if len(samples):
                    sd.play(np.clip(samples * volume, -1.0, 1.0), rate_hz)
                    sd.wait()
                if self._stop.is_set():
                    raise _Stopped()
        finally:
            self._stop.set()
            # the synthesis thread may wait for room in the queue: it is emptied until the thread ends
            while producer.is_alive():
                try:
                    ready.get(timeout=0.1)
                except queue.Empty:
                    pass

    def stop(self):
        self._stop.set()
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass


# Text-to-Speech module
class TTS:
    """Says phrases with the chosen voice. Everything that touches the speech engines happens in one thread of
    its own, so the assistant, the planner and the interface can call speak(), preview() and stop() from anywhere."""

    def __init__(self, library=None, settings=None, system_engine_factory=None, neural_engine=None):
        self.library = library or VoiceLibrary()
        self.settings = settings or TtsSettings()
        self._system_factory = system_engine_factory or (SystemEngine if pyttsx3 is not None else None)
        self.neural = neural_engine or NeuralEngine(self.library)
        self.system = None
        # Optional callback that receives the text right before it is spoken
        self.on_speak = None
        # Optional callback for the interface: on_status("busy"|"ok"|"error"), the state of the speech synthesis
        self.on_status = None
        self._jobs = queue.Queue()
        self._ready = threading.Event()
        self._speaking = threading.Event()
        self._thread = threading.Thread(target=self._work, daemon=True, name="tts")
        self._thread.start()
        self._ready.wait(timeout=10)

    # ---------- Voices ----------

    def voices(self):
        system = self.system.voices() if self.system is not None else []
        return system + self.library.installed()

    def neural_available(self):
        return self.neural.available()

    def current_voice(self):
        voices = self.voices()
        wanted = self.settings.voice
        for voice in voices:
            if voice.id == wanted:
                return voice
        # nothing chosen (or the chosen voice is gone): the default voice of Windows
        default = self.system.default_id if self.system is not None else None
        for voice in voices:
            if voice.id == default:
                return voice
        return voices[0] if voices else None

    def set_voice(self, voice_id):
        voice = next((v for v in self.voices() if v.id == voice_id), None)
        if voice is None:
            raise VoiceError("Такого голоса нет.")
        self.settings.set_voice(voice.id)
        return voice

    def next_voice(self):
        voices = self.voices()
        if not voices:
            return None
        current = self.current_voice()
        index = next((i for i, v in enumerate(voices) if current and v.id == current.id), -1)
        return self.set_voice(voices[(index + 1) % len(voices)].id)

    def find_voice(self, spoken_name):
        """A voice by a name said aloud ("денис", "ирина"): the closest of the available ones."""
        from rapidfuzz import fuzz
        spoken = spoken_name.strip().lower()
        best, best_score = None, 0
        for voice in self.voices():
            for word in re.split(r"[\s()]+", voice.name.lower()) + [voice.name.lower()]:
                if not word:
                    continue
                score = fuzz.ratio(spoken, word)
                if score > best_score:
                    best, best_score = voice, score
        return best if best_score >= 70 else None

    def set_rate(self, value):
        self.settings.set_rate(value)

    def set_volume(self, value):
        self.settings.set_volume(value)

    # ---------- Speaking ----------

    def _status(self, state):
        if self.on_status:
            try:
                self.on_status(state)
            except Exception:
                logger.exception("on_status callback error")

    def speak(self, message):
        """Says the text and returns when it has been said (or interrupted)."""
        message_str = str(message)
        logger.info("Speaking: %s", message_str)
        if self.on_speak:
            try:
                self.on_speak(message_str)
            except Exception:
                logger.exception("on_speak callback error")
        text = clean_text(message_str)
        if not text:
            return
        done = threading.Event()
        self._jobs.put((text, None, done))
        done.wait()

    def preview(self, voice_id=None, text=PREVIEW_TEXT):
        """Plays a sample with the given voice without waiting; the speech in progress is interrupted."""
        self.stop()
        self._jobs.put((text, voice_id, None))

    def stop(self):
        """Interrupts the speech in progress (the phrases waiting in the queue are still said)."""
        if self._speaking.is_set():
            self.neural.stop()
            if self.system is not None:
                self.system.stop()

    def _work(self):
        # The voices of Windows are COM objects: the thread that uses them has to initialize COM itself
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass
        if self._system_factory is not None:
            try:
                self.system = self._system_factory()
            except Exception:
                logger.exception("Голоса Windows недоступны")
        self._ready.set()
        while True:
            text, voice_id, done = self._jobs.get()
            try:
                self._say(text, voice_id)
            except Exception:
                logger.exception("TTS error")
            finally:
                if done is not None:
                    done.set()

    def _say(self, text, voice_id):
        voice = next((v for v in self.voices() if v.id == voice_id), None) if voice_id else self.current_voice()
        self._speaking.set()
        self._status("busy")
        try:
            try:
                self._say_with(voice, text)
            except _Stopped:
                pass
            except Exception as error:
                if voice is not None and voice.kind == NEURAL:
                    # The assistant must not go silent: the voice of Windows takes over for this phrase
                    logger.warning("Голос %s не сработал (%s), говорю голосом Windows", voice.name, error)
                    self._say_with(None, text)
                else:
                    raise
            self._status("ok")
        except Exception as error:
            logger.error("TTS error: %s", error)
            self._status("error")
        finally:
            self._speaking.clear()

    def _say_with(self, voice, text):
        settings = self.settings
        if voice is not None and voice.kind == NEURAL:
            self.neural.say(text, voice, settings.rate, settings.volume, settings.speaker)
        elif self.system is not None:
            self.system.say(text, voice.id if voice else None, settings.rate, settings.volume)
        else:
            raise RuntimeError("Нет ни одного доступного голоса")
