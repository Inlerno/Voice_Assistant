import logging
import pyttsx3
logger = logging.getLogger(__name__)


# Text-to-Speech module
class TTS:
    def __init__(self):
        self.engine = pyttsx3.init()
        self.engine.setProperty('rate', 200)
        self.engine.setProperty('volume', 1)
        # Optional callback that receives the text right before it is spoken
        self.on_speak = None
        # Optional callback for the interface: on_status("busy"|"ok"|"error"), the state of the speech synthesis
        self.on_status = None

    def _status(self, state):
        if self.on_status:
            try:
                self.on_status(state)
            except Exception:
                logger.exception("on_status callback error")

    def speak(self, message):
        message_str = str(message)
        logger.info("Speaking: %s", message_str)
        if self.on_speak:
            try:
                self.on_speak(message_str)
            except Exception:
                logger.exception("on_speak callback error")
        self._status("busy")
        try:
            self.engine.say(message_str)
            self.engine.runAndWait()
            self._status("ok")
        except Exception as e:
            logger.error("TTS error: %s", e)
            self._status("error")
