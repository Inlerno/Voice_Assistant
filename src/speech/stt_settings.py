"""What recognizes the commands of the user: the engine, the Whisper model and the language. Saved to data/stt.json
after every change; a damaged file or a wrong value is replaced by the default, so it never stops the assistant."""
import json
import logging
from pathlib import Path

from src.productivity.planner import DATA_DIR

logger = logging.getLogger(__name__)

SETTINGS_FILE = DATA_DIR / "stt.json"

VOSK, WHISPER = "vosk", "whisper"
ENGINES = (VOSK, WHISPER)
# Languages that Whisper is told to expect; the empty one lets it find out by itself (it may take a short phrase for
# another language, so the Russian is the default)
LANGUAGES = (("ru", "Русский"), ("en", "English"), ("", "Определять по речи"))


class SttSettings:
    def __init__(self, path=None):
        self.path = Path(path) if path else SETTINGS_FILE
        self.engine = VOSK
        self.whisper_model = None      # the id of the model of the library that Whisper uses
        self.language = "ru"
        self._load()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            logger.exception("Не удалось прочитать %s", self.path)
            return
        if not isinstance(data, dict):
            return
        self.engine = data.get("engine") if data.get("engine") in ENGINES else VOSK
        model = data.get("whisper_model")
        self.whisper_model = model if isinstance(model, str) and model.strip() else None
        language = data.get("language")
        self.language = language if language in dict(LANGUAGES) else "ru"

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(
                {"engine": self.engine, "whisper_model": self.whisper_model, "language": self.language},
                ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            logger.exception("Не удалось сохранить настройки распознавания")

    def update(self, engine=None, whisper_model=None, language=None):
        """Changes the given settings (the ones that are None stay as they are); returns True if something changed."""
        before = (self.engine, self.whisper_model, self.language)
        if engine in ENGINES:
            self.engine = engine
        if whisper_model is not None:
            self.whisper_model = whisper_model.strip() or None
        if language in dict(LANGUAGES):
            self.language = language
        changed = before != (self.engine, self.whisper_model, self.language)
        if changed:
            self.save()
        return changed
