"""A microphone that plays a recording, for the scripts that try the REAL recognition models (tests/run_*_e2e.py).

The scripts run in a process of their own where nothing is replaced by a fake except the sound card: use stub_sound() to
make "sounddevice" importable without PortAudio, and install(speech_module) to give the recognizer this microphone.
"""
import sys
import types
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
RATE = 16000
BLOCK = int(RATE * 0.2)
# A person stops speaking after the phrase: the silence is what tells the recognizer that the phrase is over
SILENCE_SECONDS = 2.0


def stub_sound():
    """"sounddevice" is imported by the recognizer; on a machine without PortAudio a stand-in is put in its place."""
    try:
        import sounddevice  # noqa: F401
    except Exception:
        stub = types.ModuleType("sounddevice")
        stub.default = types.SimpleNamespace(device=(1, 1))
        stub.query_devices = lambda *args, **kwargs: {"default_samplerate": RATE}
        sys.modules["sounddevice"] = stub


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


def install(speech_module):
    """The recognizer listens to RecordedMicrophone.pcm instead of the sound card."""
    speech_module.sd = types.SimpleNamespace(
        default=types.SimpleNamespace(device=(0, 0)),
        query_devices=lambda *args, **kwargs: {"default_samplerate": RATE},
        RawInputStream=RecordedMicrophone,
    )


def recording(name):
    """The 16-bit mono PCM of tests/data/<name>.ogg as the decoder of voice messages gives it (16 kHz)."""
    from src.audio.audio_decode import decode_audio
    return decode_audio((DATA / f"{name}.ogg").read_bytes()).pcm
