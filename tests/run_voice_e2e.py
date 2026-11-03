"""The whole way of a voice message with the REAL speech recognition, as a script (tests/test_voice_e2e.py runs it).

A person "sends" three voice messages (OGG with Opus, like Telegram makes them: tests/data) to the bot. The bot
downloads each file from a stand-in of Telegram, decodes it and lets the real Vosk model (the small one that comes with
the program) recognize it. The commands are run by a stand-in of the assistant that only writes what it was asked to do.

It is a script of its own, not a part of the test process: other tests replace "vosk" with a fake, and a recognizer
must be tried in a process where it is the real one. The last line of the output is a JSON with the result.
Exit code 77: there is no vosk or no model here, nothing could be tried.
"""
import json
import sys
import time
import types
from concurrent.futures import Future
from pathlib import Path
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

MODEL = ROOT / "src" / "assets" / "vosk-model-small-ru-0.22"
try:
    import vosk
except Exception:
    sys.exit(77)
if not (MODEL / "am").is_dir():
    sys.exit(77)
try:
    import sounddevice  # noqa: F401
except Exception:                                           # PortAudio is not there (a build server)
    stub = types.ModuleType("sounddevice")
    stub.default = types.SimpleNamespace(device=(1, 1))
    stub.query_devices = lambda *args, **kwargs: {"default_samplerate": 16000}
    sys.modules["sounddevice"] = stub

from fake_telegram import TOKEN, FakeTelegram  # noqa: E402
from src.core.remote import Verdict, normalize_query  # noqa: E402
from src.integrations.telegram.bot import TelegramBot  # noqa: E402
from src.integrations.telegram.settings import TelegramSettings  # noqa: E402
from src.speech.speech_module import SpeechRecognizer  # noqa: E402

OWNER = 42
DANGEROUS = {"выключи компьютер": "выключит компьютер"}


class Host:
    """The assistant as the bot sees it: the recognizer is the real one, the commands only leave a note."""

    def __init__(self, model):
        self.recognizer = types.SimpleNamespace(command_model=model)
        self.ran, self.notes = [], []

    def check(self, text):
        query = normalize_query(text)
        return Verdict(query, DANGEROUS.get(query))

    def run(self, text, io, note=None):
        self.ran.append(text)
        self.notes.append(note)
        io.say(f"Выполнено: {text}")
        future = Future()
        future.set_result(None)
        return future

    def transcribe(self, pcm, rate):
        return SpeechRecognizer.transcribe(self.recognizer, pcm, rate)


def wait(check, what, seconds=60):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return
        time.sleep(0.05)
    raise SystemExit(f"timeout: {what}")


def main():
    vosk.SetLogLevel(-1)
    host = Host(vosk.Model(str(MODEL)))
    with TemporaryDirectory() as folder:
        fake = FakeTelegram()
        settings = TelegramSettings(Path(folder) / "telegram.json")
        settings.update(token=TOKEN, api_base=fake.url, enabled=True)
        settings.link(OWNER, "Анна", "anna")
        bot = TelegramBot(settings, host, trust_env=False)
        try:
            bot.start()
            wait(lambda: bot.state == "ok", "the bot did not connect")
            result = {"heard": {}, "answers": {}}
            for name in ("timer", "tasks"):
                fake.voice(OWNER, (HERE / "data" / f"{name}.ogg").read_bytes(), duration=2)
                heard = fake.wait_text("🎙", OWNER)
                wait(lambda: fake.wait_text("Выполнено", OWNER, timeout=0.01) is not None, f"no answer to {name}")
                result["heard"][name] = heard["text"] if heard else None
                result["answers"][name] = [t for t in fake.texts(OWNER) if "Выполнено" in t][-1]
                fake.sent.clear()
            # A dangerous command is recognized, but it waits for a tap on the button
            fake.voice(OWNER, (HERE / "data" / "shutdown.ogg").read_bytes(), duration=2)
            question = fake.wait_text("Выполнить?", OWNER)
            result["heard"]["shutdown"] = question["text"] if question else None
            result["ran_before_the_tap"] = list(host.ran)
            button = question["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
            fake.tap(OWNER, button)
            wait(lambda: len(host.ran) == 3, "the command did not run after the tap")
            result["ran"] = list(host.ran)
            result["notes"] = list(host.notes)
        finally:
            bot.stop()
            fake.close()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
