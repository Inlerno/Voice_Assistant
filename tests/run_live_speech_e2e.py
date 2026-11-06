"""The words of a command appear while it is being said: the REAL Vosk model hears a recording as if it came from the
microphone, block after block, and the recognizer passes on what it has understood so far
(tests/test_live_speech_e2e.py runs this script).

It is a script of its own, not a part of the test process: other tests replace "vosk" with a fake, and a recognizer
must be tried in a process where it is the real one. The last line of the output is a JSON with the result.
Exit code 77: there is no vosk or no model here, nothing could be tried.
"""
import json
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

MODEL = ROOT / "src" / "assets" / "vosk-model-small-ru-0.22"
try:
    import vosk  # noqa: F401
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

from src.audio.audio_decode import decode_audio  # noqa: E402
from src.speech import speech_module as sm  # noqa: E402

RATE = 16000
BLOCK = int(RATE * 0.2)
# A person stops speaking after the phrase: the silence is what tells the recognizer that the phrase is over
SILENCE_SECONDS = 2.0


class RecordedMicrophone:
    """What sounddevice.RawInputStream does, with a recording instead of a microphone."""

    pcm = b""

    def __init__(self, samplerate, blocksize, device, dtype, channels, callback):
        self.callback = callback

    def __enter__(self):
        pcm = RecordedMicrophone.pcm + bytes(int(SILENCE_SECONDS * RATE) * 2)
        step = BLOCK * 2
        for offset in range(0, len(pcm) - step + 1, step):
            self.callback(pcm[offset:offset + step], BLOCK, None, None)
        return self

    def __exit__(self, *exc):
        return False


def listen_to(recording):
    RecordedMicrophone.pcm = decode_audio((HERE / "data" / recording).read_bytes()).pcm
    events = []
    recognizer = sm.SpeechRecognizer(str(MODEL), input_device=0, profile=object(), big_model_path=None)
    recognizer.on_partial = lambda text: events.append(["partial", text])
    recognizer.on_phrase = lambda text, stats: events.append(["phrase", text])
    result = recognizer.listen_command()
    return {"result": result, "events": events}


sm.sd = types.SimpleNamespace(
    default=types.SimpleNamespace(device=(0, 0)),
    query_devices=lambda *args, **kwargs: {"default_samplerate": RATE},
    RawInputStream=RecordedMicrophone,
)

print(json.dumps({name: listen_to(f"{name}.ogg") for name in ("timer", "tasks", "shutdown")}, ensure_ascii=False))
