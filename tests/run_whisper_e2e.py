"""A REAL Whisper model recognizes recordings of commands (tests/test_whisper_e2e.py runs this script).

Three ways in which the program uses Whisper are tried with the recordings of tests/data:
  - the engine itself: the text it makes, how long it takes, and what the planner does with that text;
  - SpeechRecognizer.transcribe(), the way a voice message of Telegram is recognized;
  - SpeechRecognizer.listen_command() with a recording instead of a microphone: the real small Vosk model finds the
    end of the phrase and shows its words, the real Whisper writes the final text.

Which model is tried: the folder in ASTRA_WHISPER_MODEL (the folder of a model of data/stt/whisper, or the one that
sherpa-onnx unpacks from its archive), otherwise the best model that is downloaded to data/stt/whisper.

It is a script of its own, not a part of the test process: other tests replace "vosk" with a fake, and a recognizer
must be tried in a process where it is the real one. The last line of the output is a JSON with the result.
Exit code 77: there is no sherpa-onnx, no Vosk model or no Whisper model here, nothing could be tried.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
# The isolated mode (-I) does not put the folder of the script on the path
sys.path.insert(0, str(HERE))

VOSK_MODEL = ROOT / "src" / "assets" / "vosk-model-small-ru-0.22"
try:
    import sherpa_onnx  # noqa: F401
    import vosk  # noqa: F401
except Exception:
    sys.exit(77)
if not (VOSK_MODEL / "am").is_dir():
    sys.exit(77)

import recorded_mic  # noqa: E402

recorded_mic.stub_sound()

from src.core.remote import normalize_query  # noqa: E402
from src.productivity.apps import AppLauncher  # noqa: E402
from src.productivity.planner import Planner  # noqa: E402
from src.productivity.skills import Productivity  # noqa: E402
from src.speech import speech_module as sm  # noqa: E402
from src.speech.whisper_engine import CATALOG, MODEL_FILES, WhisperEngine, WhisperLibrary  # noqa: E402

RECORDINGS = ("timer", "tasks", "shutdown")
# What the model written for the engine may be called: the best one is tried first
PREFERRED = ("small", "turbo", "large-v3", "base", "tiny")


def library_folder(folder, scratch):
    """The folder of a model in the form the program keeps them. A folder of an archive of sherpa-onnx
    (base-encoder.int8.onnx ...) is put into that form with links."""
    folder = Path(folder)
    if all((folder / name).is_file() for name in MODEL_FILES):
        return folder
    for suffix, own in (("-encoder.int8.onnx", MODEL_FILES[0]), ("-decoder.int8.onnx", MODEL_FILES[1]),
                        ("-tokens.txt", MODEL_FILES[2])):
        found = sorted(folder.glob(f"*{suffix}"))
        if not found:
            return None
        target = Path(scratch) / folder.name.replace("sherpa-onnx-whisper-", "") / own
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(found[0].resolve())
    return target.parent


def find_model(scratch):
    given = os.environ.get("ASTRA_WHISPER_MODEL")
    if given:
        return library_folder(given, scratch)
    library = WhisperLibrary()
    installed = library.installed()
    for name in PREFERRED:
        if name in installed:
            return library.folder(name)
    return library.folder(installed[0]) if installed else None


def planner_effect(text, folder):
    """What the planner does with a phrase: a timer is set, a task is added; None if it is not its phrase."""
    planner = Planner(path=str(Path(folder) / "planner.json"))
    skills = Productivity(planner, AppLauncher(path=str(Path(folder) / "apps.json"), opener=lambda t: None,
                                               shortcut_dirs=[]))
    answer = skills.handle(normalize_query(text))
    return {"answer": answer, "timers": [item["length"] for item in planner.items("timer")],
            "tasks": [task["text"] for task in planner.tasks()]}


def main():
    with tempfile.TemporaryDirectory() as scratch:
        folder = find_model(Path(scratch) / "models")
        if folder is None:
            sys.exit(77)
        title = next((entry.title for entry in CATALOG if entry.id == folder.name), folder.name)
        result = {"model": folder.name, "title": title, "engine": {}, "transcribe": {}, "listen": {}}

        started = time.perf_counter()
        engine = WhisperEngine(folder, "ru")
        engine.load()
        result["load_s"] = time.perf_counter() - started

        recognizer = sm.SpeechRecognizer(str(VOSK_MODEL), input_device=0, profile=object(), big_model_path=None)
        loader = recognizer.use_engine("whisper", folder, "ru")
        loader.join(timeout=300)
        result["recognizer_engine"] = recognizer.engine
        recorded_mic.install(sm)

        for name in RECORDINGS:
            pcm = recorded_mic.recording(name)

            started = time.perf_counter()
            raw = engine.transcribe(pcm, recorded_mic.RATE)
            spent = (time.perf_counter() - started) * 1000
            spoken = sm.to_spoken(raw)
            result["engine"][name] = {"raw": raw, "spoken": spoken, "ms": spent, "audio_s": len(pcm) / 2 / 16000,
                                      "effect": planner_effect(spoken, Path(scratch) / f"plan-{name}")}

            text, stats = recognizer.transcribe(pcm, recorded_mic.RATE)
            result["transcribe"][name] = {"text": text, "stats": stats}

            recorded_mic.RecordedMicrophone.pcm = pcm
            events = []
            recognizer.on_partial = lambda value, log=events: log.append(["partial", value])
            recognizer.on_phrase = lambda value, info, log=events: log.append(["phrase", value, info])
            recognizer.on_state = lambda value, log=events: log.append(["state", value])
            heard = recognizer.listen_command()
            result["listen"][name] = {"result": heard, "events": events}
    print(json.dumps(result, ensure_ascii=False))


main()
