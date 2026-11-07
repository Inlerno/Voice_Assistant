"""The choice of the user (the engine, the Whisper model, the language) put to work on the recognizer.

The window never touches the recognizer or the files of the models itself: it asks this class, so the same rules
(a model that is not downloaded cannot be chosen, a model that is deleted is replaced by Vosk) hold for every caller.
"""
import logging

from src.speech.stt_settings import VOSK, WHISPER, SttSettings
from src.speech.whisper_engine import SttError, WhisperLibrary

logger = logging.getLogger(__name__)


class SttControl:
    def __init__(self, recognizer, settings=None, library=None):
        self.recognizer = recognizer
        self.settings = settings if settings is not None else SttSettings()
        self.library = library if library is not None else WhisperLibrary()

    # Called once when the assistant starts: Whisper, if the user has chosen it, loads in the background.
    # Vosk is the engine without any settings, there is nothing to start for it.
    def start(self):
        if self.settings.engine == WHISPER:
            self.apply()

    # Put the saved choice to work: the recognizer switches to Whisper when its model has been loaded
    def apply(self):
        settings, library = self.settings, self.library
        if settings.engine == WHISPER:
            model = settings.whisper_model
            folder = library.folder(model) if model and library.is_installed(model) else None
            self.recognizer.use_engine(WHISPER, folder, settings.language)
        else:
            self.recognizer.use_engine(VOSK)

    # Choose the engine: "vosk" or "whisper"
    def set_engine(self, engine):
        self.settings.update(engine=engine)
        self.apply()

    # Choose the Whisper model (it has to be downloaded) and/or the language it listens for
    def set_whisper(self, model_id=None, language=None):
        if model_id is not None and not self.library.is_installed(model_id):
            raise SttError("Эта модель ещё не скачана.")
        changed = self.settings.update(whisper_model=model_id, language=language)
        if changed and self.settings.engine == WHISPER:
            self.apply()

    # Delete a downloaded model; if Whisper was using it, Vosk takes over again
    def remove_model(self, model_id):
        was_chosen = self.settings.whisper_model == model_id
        self.library.remove(model_id)
        if was_chosen:
            self.settings.update(whisper_model="", engine=VOSK)
            self.apply()

    # What the settings page shows about the recognition
    def info(self):
        recognizer, settings = self.recognizer, self.settings
        return {
            "engine": settings.engine,
            "active": recognizer.engine,
            "whisper_state": recognizer.whisper_state,
            "whisper_model": settings.whisper_model,
            "language": settings.language,
            "installed": self.library.installed(),
        }
