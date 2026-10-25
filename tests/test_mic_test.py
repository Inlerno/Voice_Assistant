import json
import sys
import threading
import types
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SAMPLERATE = 16000
BLOCK = int(SAMPLERATE * 0.1)
SCENARIO = {}


# Stand-ins for the audio hardware and the speech model, so the tests run anywhere
class FakeStream:
    def __init__(self, samplerate, blocksize, device, dtype, channels, callback):
        self.callback = callback
        self.blocksize = blocksize

    def __enter__(self):
        if SCENARIO.get("stream_error"):
            raise RuntimeError("device busy")
        for _ in range(SCENARIO.get("blocks", 60)):
            self.callback(SCENARIO["signal"](self.blocksize).astype(np.int16).tobytes(), self.blocksize, None, None)
        return self

    def __exit__(self, *args):
        return False


class FakeRecognizer:
    def __init__(self, model, samplerate):
        self.received = 0

    def AcceptWaveform(self, data):
        self.received += len(data)
        return False

    def FinalResult(self):
        return json.dumps({"text": SCENARIO.get("text", "") if self.received else ""})


def query_devices(device=None, kind=None):
    if SCENARIO.get("no_device"):
        raise ValueError("Error querying device")
    return {"default_samplerate": SAMPLERATE}


fake_sd = types.ModuleType("sounddevice")
fake_sd.query_devices = query_devices
fake_sd.RawInputStream = FakeStream
fake_vosk = types.ModuleType("vosk")
fake_vosk.KaldiRecognizer = FakeRecognizer
sys.modules["sounddevice"] = fake_sd
sys.modules["vosk"] = fake_vosk

from src.audio import mic_test  # noqa: E402


def sine(amplitude):
    return lambda n: amplitude * 32767 * np.sin(2 * np.pi * 220 * np.arange(n) / SAMPLERATE)


def square(amplitude):
    return lambda n: amplitude * 32767 * np.where(np.arange(n) % 2 == 0, 1, -1)


class SignalMathTest(unittest.TestCase):
    def test_block_level(self):
        self.assertAlmostEqual(mic_test.block_level_db(square(1.0)(BLOCK).astype(np.int16)), 0.0, delta=0.1)
        self.assertAlmostEqual(mic_test.block_level_db(square(0.5)(BLOCK).astype(np.int16)), -6.0, delta=0.1)
        self.assertEqual(mic_test.block_level_db(np.zeros(BLOCK, dtype=np.int16)), -100.0)
        self.assertEqual(mic_test.block_level_db(np.array([], dtype=np.int16)), -100.0)

    def test_meter_range(self):
        self.assertEqual(mic_test.db_to_meter(-100.0), 0.0)
        self.assertEqual(mic_test.db_to_meter(-60.0), 0.0)
        self.assertAlmostEqual(mic_test.db_to_meter(-30.0), 0.5)
        self.assertEqual(mic_test.db_to_meter(0.0), 1.0)
        self.assertEqual(mic_test.db_to_meter(6.0), 1.0)

    def test_verdicts(self):
        self.assertEqual(mic_test.evaluate_signal(-70.0, 0.0).status, "silent")
        self.assertEqual(mic_test.evaluate_signal(-35.0, 0.0).status, "quiet")
        self.assertEqual(mic_test.evaluate_signal(-10.0, 0.0).status, "ok")
        self.assertEqual(mic_test.evaluate_signal(-0.5, 0.02).status, "loud")
        # Clipping does not matter when there is no signal at all
        self.assertEqual(mic_test.evaluate_signal(-70.0, 0.5).status, "silent")


class CheckMicrophoneTest(unittest.TestCase):
    def run_check(self, **kwargs):
        levels = []
        result = mic_test.check_microphone(
            0, duration=5.0, model=kwargs.pop("model", object()), on_level=levels.append, **kwargs
        )
        return result, levels

    def test_good_signal_with_recognition(self):
        SCENARIO.clear()
        SCENARIO.update(signal=sine(0.3), text="раз два три")
        result, levels = self.run_check()
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.text, "раз два три")
        self.assertEqual(len(levels), 50)  # 5 seconds in 0.1 second blocks
        self.assertTrue(all(0.0 <= level <= 1.0 for level in levels))
        self.assertGreater(levels[0], 0.5)

    def test_without_model_there_is_no_text(self):
        SCENARIO.clear()
        SCENARIO.update(signal=sine(0.3), text="раз два три")
        result, _ = self.run_check(model=None)
        self.assertEqual(result.status, "ok")
        self.assertIsNone(result.text)

    def test_silence(self):
        SCENARIO.clear()
        SCENARIO.update(signal=lambda n: np.zeros(n))
        result, levels = self.run_check()
        self.assertEqual(result.status, "silent")
        self.assertEqual(max(levels), 0.0)
        self.assertIsNone(result.text)

    def test_quiet_voice(self):
        SCENARIO.clear()
        SCENARIO.update(signal=sine(0.02))
        result, _ = self.run_check()
        self.assertEqual(result.status, "quiet")

    def test_clipping(self):
        SCENARIO.clear()
        SCENARIO.update(signal=square(1.0))
        result, _ = self.run_check()
        self.assertEqual(result.status, "loud")

    def test_device_error(self):
        SCENARIO.clear()
        SCENARIO.update(signal=sine(0.3), stream_error=True)
        result, levels = self.run_check()
        self.assertEqual(result.status, "error")
        self.assertIn("device busy", result.message)
        self.assertEqual(levels, [])

    def test_unknown_device(self):
        SCENARIO.clear()
        SCENARIO.update(signal=sine(0.3), no_device=True)
        result, _ = self.run_check()
        self.assertEqual(result.status, "error")

    def test_stop_event(self):
        SCENARIO.clear()
        SCENARIO.update(signal=sine(0.3))
        stop = threading.Event()
        stop.set()
        result, levels = self.run_check(stop_event=stop)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(levels, [])

    def test_stream_without_data(self):
        SCENARIO.clear()
        SCENARIO.update(signal=sine(0.3), blocks=0)
        result, _ = self.run_check()
        self.assertEqual(result.status, "error")


if __name__ == "__main__":
    unittest.main()
