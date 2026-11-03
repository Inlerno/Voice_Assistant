"""The decoding of voice messages: OGG/Opus and other recordings become 16-bit mono PCM of 16 kHz for Vosk."""
import io
import os
import random
import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import soundfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.audio import audio_decode as A  # noqa: E402
from src.audio.audio_decode import AudioDecodeError, decode_audio, resample  # noqa: E402


def tone(frequency, seconds, rate, amplitude=0.5):
    t = np.arange(int(rate * seconds)) / rate
    return (amplitude * np.sin(2 * np.pi * frequency * t)).astype("float32")


def encode(samples, rate, container="WAV", subtype="PCM_16"):
    buffer = io.BytesIO()
    soundfile.write(buffer, samples, rate, format=container, subtype=subtype)
    return buffer.getvalue()


def samples_of(decoded):
    return np.frombuffer(decoded.pcm, dtype="<i2").astype("float32") / 32768.0


def peak_frequency(samples, rate):
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    return float(np.argmax(spectrum)) * rate / len(samples)


def level(samples):
    """The strength of the signal, as a root of its mean square."""
    return float(np.sqrt(np.mean(np.square(samples))))


class ResampleTests(unittest.TestCase):
    def test_the_same_rate_changes_nothing(self):
        data = tone(300, 0.2, 16000)
        self.assertTrue(np.array_equal(resample(data, 16000, 16000), data))

    def test_48k_to_16k_keeps_the_tone_and_its_strength(self):
        out = resample(tone(1000, 1.0, 48000), 48000, 16000)
        self.assertEqual(len(out), 16000)
        # the edges hold the start of the filter: the middle tells the truth
        middle = out[2000:-2000]
        self.assertAlmostEqual(peak_frequency(middle, 16000), 1000, delta=10)
        self.assertAlmostEqual(level(middle), 0.5 / np.sqrt(2), delta=0.01)

    def test_what_the_new_rate_cannot_hold_does_not_turn_into_noise(self):
        # 12 kHz is above the half of 16 kHz: without the filter it would fold into 4 kHz
        out = resample(tone(12000, 1.0, 48000), 48000, 16000)
        self.assertLess(level(out[2000:-2000]), 0.005)

    def test_a_rate_that_is_not_a_multiple(self):
        out = resample(tone(1000, 1.0, 44100), 44100, 16000)
        self.assertAlmostEqual(len(out), 16000, delta=2)
        self.assertAlmostEqual(peak_frequency(out[2000:-2000], 16000), 1000, delta=10)
        self.assertAlmostEqual(level(out[2000:-2000]), 0.5 / np.sqrt(2), delta=0.02)

    def test_going_up(self):
        out = resample(tone(500, 1.0, 8000), 8000, 16000)
        self.assertAlmostEqual(len(out), 16000, delta=2)
        self.assertAlmostEqual(peak_frequency(out, 16000), 500, delta=10)

    def test_very_short_and_empty_sound(self):
        self.assertEqual(len(resample(np.zeros(0, "float32"), 48000, 16000)), 0)
        # shorter than the filter: no crash, a result of the right length
        self.assertEqual(len(resample(np.ones(10, "float32"), 48000, 16000)), 4)
        self.assertEqual(len(resample(np.ones(1, "float32"), 48000, 16000)), 1)


class DecodeTests(unittest.TestCase):
    def test_a_telegram_voice_message(self):
        # OGG with Opus inside, mono 48 kHz, as Telegram makes it
        data = encode(tone(700, 2.0, 48000), 48000, "OGG", "OPUS")
        decoded = decode_audio(data)
        self.assertEqual(decoded.rate, 16000)
        self.assertAlmostEqual(decoded.seconds, 2.0, delta=0.05)
        self.assertEqual(len(decoded.pcm), int(decoded.seconds * 16000) * 2)
        samples = samples_of(decoded)
        self.assertAlmostEqual(peak_frequency(samples[3000:-3000], 16000), 700, delta=15)
        self.assertGreater(level(samples), 0.2)

    def test_a_wav_of_the_right_kind_comes_out_the_same(self):
        original = (tone(440, 0.5, 16000) * 32767).astype("<i2")
        decoded = decode_audio(encode(original, 16000))
        got = np.frombuffer(decoded.pcm, dtype="<i2")
        self.assertEqual(len(got), len(original))
        self.assertLessEqual(int(np.max(np.abs(got.astype(int) - original.astype(int)))), 2)

    def test_stereo_is_mixed_down(self):
        left, right = tone(500, 1.0, 16000, 0.4), tone(500, 1.0, 16000, 0.2)
        decoded = decode_audio(encode(np.stack([left, right], axis=1), 16000))
        self.assertAlmostEqual(level(samples_of(decoded)), 0.3 / np.sqrt(2), delta=0.01)
        self.assertEqual(len(decoded.pcm), 16000 * 2)

    def test_other_rates_and_formats(self):
        for rate in (8000, 22050, 44100, 48000):
            with self.subTest(rate=rate):
                decoded = decode_audio(encode(tone(600, 1.0, rate), rate, "FLAC", "PCM_16"))
                self.assertEqual(decoded.rate, 16000)
                self.assertAlmostEqual(decoded.seconds, 1.0, delta=0.02)
                self.assertAlmostEqual(peak_frequency(samples_of(decoded)[2000:-2000], 16000), 600, delta=12)

    def test_loud_sound_is_clipped_not_wrapped(self):
        decoded = decode_audio(encode(np.full(16000, 1.0, "float32"), 16000, "WAV", "FLOAT"))
        self.assertEqual(int(np.frombuffer(decoded.pcm, dtype="<i2").min()), 32767)

    def test_silence_is_sound_too(self):
        decoded = decode_audio(encode(np.zeros(8000, "float32"), 16000))
        self.assertEqual(decoded.pcm, bytes(16000))


class RefusalTests(unittest.TestCase):
    def refused(self, data, **options):
        with self.assertRaises(AudioDecodeError) as caught:
            decode_audio(data, **options)
        return str(caught.exception)

    def test_nothing(self):
        self.assertIn("Пустая", self.refused(b""))
        self.assertIn("Пустая", self.refused(None))

    def test_not_a_recording(self):
        self.assertIn("Не удалось прочитать", self.refused(b"this is a text, not a sound" * 50))
        self.assertIn("Не удалось прочитать", self.refused(os.urandom(5000)))

    def test_a_recording_that_is_too_long(self):
        # 10 minutes of silence take little room in a FLAC file, and the limit is checked before the sound is read
        data = encode(np.zeros(8000 * 600, "float32"), 8000, "FLAC", "PCM_16")
        self.assertIn("слишком длинная", self.refused(data))
        self.assertEqual(decode_audio(encode(np.zeros(8000 * 5, "float32"), 8000, "FLAC", "PCM_16"),
                                      max_seconds=10).rate, 16000)
        self.assertIn("слишком длинная", self.refused(encode(np.zeros(8000 * 5, "float32"), 8000, "FLAC", "PCM_16"),
                                                      max_seconds=2))

    def test_a_file_that_is_too_big(self):
        with mock.patch.object(A, "MAX_BYTES", 1000):
            self.assertIn("слишком большой", self.refused(bytes(1001)))

    def test_a_damaged_file_is_refused_or_decoded_never_a_crash(self):
        data = encode(tone(500, 1.0, 48000), 48000, "OGG", "OPUS")
        generator = random.Random(7)
        for cut in (10, 40, len(data) // 3, len(data) - 5):
            with self.subTest(cut=cut):
                try:
                    decode_audio(data[:cut])
                except AudioDecodeError:
                    pass
        for _ in range(20):
            broken = bytearray(data)
            for _ in range(5):
                broken[generator.randrange(len(broken))] = generator.randrange(256)
            try:
                decode_audio(bytes(broken))
            except AudioDecodeError:
                pass

    def test_without_soundfile_the_text_tells_what_to_do(self):
        with mock.patch.dict(sys.modules, {"soundfile": None}):
            text = self.refused(b"data")
        self.assertIn("soundfile", text)
        self.assertIn("Astra.bat", text)

    def test_the_error_is_written_for_the_user(self):
        # no names of files, no library errors: the person sees this text in a chat
        text = self.refused(b"garbage" * 100)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("sf_", text)


if __name__ == "__main__":
    unittest.main()
