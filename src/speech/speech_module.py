import json
import logging
import queue
import sounddevice as sd
import vosk

logger = logging.getLogger(__name__)


class SpeechRecognizer:
    def __init__(self, model_path="src/assets/vosk-model-small-ru-0.22", input_device=None):
        self.model = vosk.Model(model_path)
        self.input_device = input_device or sd.default.device[0]
        self.audio_queue = queue.Queue()

    # Listen for the activation word
    def listen_for_activation(self, activation_words, callback=None):
        samplerate = int(sd.query_devices(self.input_device, 'input')['default_samplerate'])
        rec = vosk.KaldiRecognizer(self.model, samplerate)

        # Drop stale audio left from the previous session (for example the assistant's own voice)
        with self.audio_queue.mutex:
            self.audio_queue.queue.clear()

        def stream_callback(indata, frames, time_info, status):
            if status:
                logger.warning("Audio stream status: %s", status)
            self.audio_queue.put(bytes(indata))

        with sd.RawInputStream(
            samplerate=samplerate,
            blocksize=8000,
            device=self.input_device,
            dtype='int16',
            channels=1,
            callback=stream_callback
        ):
            logger.info("Ожидание активационного слова 'Астра'...")
            while True:
                data = self.audio_queue.get()
                if rec.AcceptWaveform(data):
                    result = json.loads(rec.Result())
                    recognized_text = result.get('text', '').lower()
                    if any(word in recognized_text for word in activation_words):
                        logger.info("Активационное слово распознано.")
                        if callback:
                            callback()
                        return

    # Recognize a voice command.
    # Recording stops as soon as Vosk detects the end of the phrase, so the
    # command is recognized right after the user stops speaking.
    #   max_duration      - hard limit for the whole recording, in seconds
    #   no_speech_timeout - give up if nothing was said within this time, in seconds
    def listen_command(self, max_duration=8, no_speech_timeout=5):
        try:
            samplerate = int(sd.query_devices(self.input_device, 'input')['default_samplerate'])
            rec = vosk.KaldiRecognizer(self.model, samplerate)
            block_seconds = 0.2
            blocksize = int(samplerate * block_seconds)
            command_queue = queue.Queue()

            def stream_callback(indata, frames, time_info, status):
                if status:
                    logger.warning("Audio stream status: %s", status)
                command_queue.put(bytes(indata))

            elapsed = 0.0
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
                while elapsed < max_duration:
                    try:
                        data = command_queue.get(timeout=1)
                    except queue.Empty:
                        logger.warning("Audio stream produced no data")
                        break
                    elapsed += block_seconds

                    if rec.AcceptWaveform(data):
                        # Vosk detected the end of the phrase: its text is in Result()
                        result_json = rec.Result()
                        break

                    if not speech_started and json.loads(rec.PartialResult()).get('partial'):
                        speech_started = True
                    if not speech_started and elapsed >= no_speech_timeout:
                        return None

            if result_json is None:
                # The limit was reached in the middle of a phrase: FinalResult returns what was heard
                result_json = rec.FinalResult()
            query = json.loads(result_json).get('text', '').lower().strip()
            if query:
                logger.info("Recognized: %s", query)
                return query
            return None
        except Exception as e:
            logger.error("Audio recording error: %s", e)
            return None