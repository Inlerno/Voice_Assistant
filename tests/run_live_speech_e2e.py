"""The words of a command appear while it is being said: the REAL Vosk model hears a recording as if it came from the
microphone, block after block, and the recognizer passes on what it has understood so far
(tests/test_live_speech_e2e.py runs this script).

It is a script of its own, not a part of the test process: other tests replace "vosk" with a fake, and a recognizer
must be tried in a process where it is the real one. The last line of the output is a JSON with the result.
Exit code 77: there is no vosk or no model here, nothing could be tried.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
# The isolated mode (-I) does not put the folder of the script on the path
sys.path.insert(0, str(HERE))

MODEL = ROOT / "src" / "assets" / "vosk-model-small-ru-0.22"
try:
    import vosk  # noqa: F401
except Exception:
    sys.exit(77)
if not (MODEL / "am").is_dir():
    sys.exit(77)

import recorded_mic  # noqa: E402

recorded_mic.stub_sound()

from src.speech import speech_module as sm  # noqa: E402


def listen_to(name):
    recorded_mic.RecordedMicrophone.pcm = recorded_mic.recording(name)
    events = []
    recognizer = sm.SpeechRecognizer(str(MODEL), input_device=0, profile=object(), big_model_path=None)
    recognizer.on_partial = lambda text: events.append(["partial", text])
    recognizer.on_phrase = lambda text, stats: events.append(["phrase", text])
    result = recognizer.listen_command()
    return {"result": result, "events": events}


recorded_mic.install(sm)

print(json.dumps({name: listen_to(name) for name in ("timer", "tasks", "shutdown")}, ensure_ascii=False))
