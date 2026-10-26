import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The audio libraries are not needed here: nothing is recorded or recognized, only the comparison is tested
fake_sd = types.ModuleType("sounddevice")
fake_sd.default = types.SimpleNamespace(device=(0, 0))
fake_vosk = types.ModuleType("vosk")
fake_vosk.Model = lambda path: object()
sys.modules["sounddevice"] = fake_sd
sys.modules["vosk"] = fake_vosk

from src.speech import voice_profile as vp  # noqa: E402
from src.speech.speech_module import SpeechRecognizer, crop_to_words  # noqa: E402

RATE = 16000
# Two made-up words: a sequence of vowel-like sounds, each described by two formant frequencies
WORD_A = [(700, 1100), (400, 2200), (300, 2800), (750, 1200)]
WORD_B = [(300, 800), (500, 1600), (650, 1000), (350, 2400)]


# A voiced "word" as 16-bit PCM with silence around it; the arguments imitate how a person varies
def synth(formants, seed=0, stretch=1.0, gain=0.3, noise=0.002, pitch=130.0):
    rng = np.random.default_rng(seed)
    parts = []
    for f1, f2 in formants:
        n = int(0.12 * stretch * RATE * rng.uniform(0.9, 1.1))
        t = np.arange(n) / RATE
        freqs = pitch * rng.uniform(0.97, 1.03) * np.arange(1, 40)
        amplitude = np.exp(-0.5 * ((freqs - f1) / 120) ** 2) + 0.7 * np.exp(-0.5 * ((freqs - f2) / 180) ** 2)
        parts.append((amplitude[:, None] * np.sin(2 * np.pi * freqs[:, None] * t)).sum(axis=0) * np.hanning(n) ** 0.3)
    word = np.concatenate(parts)
    word = word / np.abs(word).max() * gain
    silence = np.zeros(int(0.4 * RATE))
    signal = np.concatenate([silence, word, silence]) + rng.normal(0, noise, word.size + silence.size * 2)
    return (np.clip(signal, -1, 1) * 32767).astype(np.int16).tobytes()


def make_profile(path, seeds=range(5)):
    templates = []
    for seed in seeds:
        templates += vp.sample_features(synth(WORD_A, seed=seed, stretch=0.9 + 0.05 * seed), RATE, seed=seed)
    profile = vp.VoiceProfile(path)
    profile.enroll("астра", templates)
    return profile


class FeaturesTest(unittest.TestCase):
    def test_features_of_a_word(self):
        features = vp.word_features(synth(WORD_A), RATE)
        self.assertEqual(features.shape[1], vp.MFCC_COUNT)
        self.assertTrue(30 < features.shape[0] < 120)

    def test_silence_and_noise_are_not_a_word(self):
        self.assertIsNone(vp.word_features(np.zeros(RATE, dtype=np.int16).tobytes(), RATE))
        noise = (np.random.default_rng(1).normal(0, 3, RATE) * 1).astype(np.int16).tobytes()
        self.assertIsNone(vp.word_features(noise, RATE))
        self.assertIsNone(vp.word_features(b"", RATE))

    def test_too_long_phrase_is_not_the_wake_word(self):
        long_phrase = b"".join(synth(WORD_A, seed=s) for s in range(4))
        self.assertIsNone(vp.word_features(long_phrase, RATE))

    def test_volume_does_not_matter(self):
        # The same recording, only the microphone gain differs (the noise grows with it)
        quiet = vp.word_features(synth(WORD_A, seed=2, gain=0.05, noise=0.00025), RATE)
        loud = vp.word_features(synth(WORD_A, seed=2, gain=0.5, noise=0.0025), RATE)
        self.assertLess(vp.dtw_distance(quiet, loud), 0.3)

    def test_other_sample_rates_are_converted(self):
        pcm = (np.repeat(np.frombuffer(synth(WORD_A), dtype=np.int16), 3)).tobytes()
        converted = vp.to_float_16k(pcm, 48000)
        self.assertAlmostEqual(converted.size / RATE, len(pcm) / 2 / 48000, delta=0.01)
        self.assertIsNotNone(vp.word_features(pcm, 48000))

    def test_sample_gets_a_noisy_copy(self):
        features = vp.sample_features(synth(WORD_A), RATE)
        self.assertEqual(len(features), 2)
        self.assertEqual(vp.sample_features(np.zeros(RATE, dtype=np.int16).tobytes(), RATE), [None])


class ScoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.profile = make_profile(Path(self.dir.name) / "profile.json")

    def score(self, pcm):
        return self.profile.score(vp.word_features(pcm, RATE))

    def test_the_same_word_is_accepted(self):
        for seed, stretch, gain in [(10, 1.0, 0.3), (11, 0.9, 0.25), (12, 1.1, 0.5), (13, 1.0, 0.2)]:
            score = self.score(synth(WORD_A, seed=seed, stretch=stretch, gain=gain))
            self.assertTrue(self.profile.accepts(score), f"score {score:.3f} threshold {self.profile.threshold:.3f}")

    def test_another_word_is_rejected(self):
        for seed in (20, 21, 22):
            score = self.score(synth(WORD_B, seed=seed))
            self.assertFalse(self.profile.accepts(score), f"score {score:.3f} threshold {self.profile.threshold:.3f}")

    def test_different_length_is_rejected(self):
        self.assertEqual(vp.dtw_distance(np.zeros((10, 12)), np.zeros((40, 12))), float("inf"))

    def test_sensitivity_changes_the_threshold(self):
        base = self.profile.active_threshold
        self.profile.set_sensitivity(0.8)
        self.assertAlmostEqual(self.profile.active_threshold, base * 0.8)
        self.profile.set_sensitivity(100)
        self.assertEqual(self.profile.sensitivity, vp.SENSITIVITY_MAX)
        self.profile.set_sensitivity(0)
        self.assertEqual(self.profile.sensitivity, vp.SENSITIVITY_MIN)

    def test_empty_profile_is_not_enrolled(self):
        self.assertFalse(vp.VoiceProfile(Path(self.dir.name) / "none.json").enrolled)


class PersistenceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "profile.json"

    def test_save_and_load(self):
        profile = make_profile(self.path)
        profile.set_sensitivity(1.2)
        profile.save()
        loaded = vp.VoiceProfile.load(self.path)
        self.assertTrue(loaded.enrolled)
        self.assertEqual(loaded.word, "астра")
        self.assertEqual(len(loaded.templates), len(profile.templates))
        self.assertAlmostEqual(loaded.threshold, profile.threshold, places=4)
        self.assertAlmostEqual(loaded.base_threshold, profile.base_threshold, places=4)
        self.assertAlmostEqual(loaded.sensitivity, 1.2, places=3)
        features = vp.word_features(synth(WORD_A, seed=30), RATE)
        self.assertAlmostEqual(loaded.score(features), profile.score(features), places=2)
        self.assertFalse(self.path.with_suffix(".tmp").exists())

    def test_broken_file_gives_an_empty_profile(self):
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertLogs(vp.logger, level="ERROR"):
            profile = vp.VoiceProfile.load(self.path)
        self.assertFalse(profile.enrolled)

    def test_missing_file_gives_an_empty_profile(self):
        self.assertFalse(vp.VoiceProfile.load(self.path).enrolled)

    def test_reset_removes_the_file(self):
        profile = make_profile(self.path)
        profile.save()
        profile.reset()
        self.assertFalse(self.path.exists())
        self.assertFalse(profile.enrolled)
        self.assertEqual(profile.base_threshold, 0.0)
        profile.reset()  # a second reset is harmless

    def test_old_file_without_base_threshold(self):
        profile = make_profile(self.path)
        profile.save()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        del data["base_threshold"]
        self.path.write_text(json.dumps(data), encoding="utf-8")
        loaded = vp.VoiceProfile.load(self.path)
        self.assertAlmostEqual(loaded.base_threshold, loaded.threshold)


class AdaptationTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.profile = make_profile(Path(self.dir.name) / "profile.json")

    def test_learns_only_from_confident_phrases(self):
        features = vp.word_features(synth(WORD_A, seed=40), RATE)
        self.assertFalse(self.profile.would_adapt(self.profile.threshold * 0.95))
        self.assertFalse(self.profile.would_adapt(float("inf")))
        self.assertFalse(self.profile.adapt(features, self.profile.threshold * 0.95))
        self.assertEqual(self.profile.adaptations, 0)
        count = len(self.profile.templates)
        self.assertTrue(self.profile.adapt(features, self.profile.threshold * 0.5))
        self.assertEqual(len(self.profile.templates), count + 1)
        self.assertEqual(self.profile.adaptations, 1)

    def test_number_of_templates_is_limited(self):
        for seed in range(40, 40 + vp.MAX_TEMPLATES + 4):
            features = vp.word_features(synth(WORD_A, seed=seed, stretch=0.9 + 0.01 * (seed % 20)), RATE)
            self.profile.adapt(features, 0.0)
        self.assertEqual(len(self.profile.templates), vp.MAX_TEMPLATES)

    def test_threshold_can_not_run_away(self):
        base = self.profile.base_threshold
        # Samples that are different from each other would make a calculated threshold much higher
        words = [WORD_A, [(700, 1100), (400, 2200), (500, 1600), (750, 1200)], [(650, 1000), (400, 2200), (300, 2800)]]
        for i in range(12):
            features = vp.word_features(synth(words[i % 3], seed=60 + i, stretch=0.8 + 0.05 * (i % 5)), RATE)
            if features is not None:
                self.profile.adapt(features, 0.0)
        self.assertLessEqual(self.profile.threshold, base * vp.THRESHOLD_DRIFT_LIMIT + 1e-9)

    def test_does_not_change_an_empty_profile(self):
        empty = vp.VoiceProfile(Path(self.dir.name) / "empty.json")
        self.assertFalse(empty.adapt(vp.word_features(synth(WORD_A), RATE), 0.0))


class WakeWordCheckTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.profile = make_profile(Path(self.dir.name) / "profile.json")
        self.recognizer = SpeechRecognizer(input_device=0, profile=self.profile)
        self.heard = []
        self.recognizer.on_activation = self.heard.append

    def check(self, text, pcm):
        return self.recognizer._check_wake_word(text, pcm, RATE, 42.0, ["астра"])

    def test_owner_is_accepted(self):
        self.assertTrue(self.check("астра", synth(WORD_A, seed=70)))
        info = self.heard[-1]
        self.assertTrue(info["accepted"])
        self.assertEqual(info["decode_ms"], 42.0)
        self.assertGreater(info["dtw_ms"], 0)
        self.assertLess(info["score"], info["threshold"])

    def test_other_voice_is_rejected(self):
        self.assertFalse(self.check("астра", synth(WORD_B, seed=71)))
        self.assertFalse(self.heard[-1]["accepted"])

    def test_noise_is_rejected(self):
        self.assertFalse(self.check("астра", np.zeros(RATE, dtype=np.int16).tobytes()))
        self.assertIsNone(self.heard[-1]["score"])

    def test_other_words_do_not_wake_the_assistant(self):
        self.assertFalse(self.check("открой браузер", synth(WORD_A, seed=72)))
        self.assertFalse(self.check("[unk]", synth(WORD_A, seed=72)))
        self.assertEqual(self.heard, [])

    def test_without_a_profile_every_voice_is_accepted(self):
        self.recognizer.profile = vp.VoiceProfile(Path(self.dir.name) / "none.json")
        self.assertTrue(self.check("астра", synth(WORD_B, seed=73)))
        self.assertIsNone(self.heard[-1]["threshold"])

    def test_a_broken_interface_callback_does_not_stop_the_check(self):
        def broken(_info):
            raise RuntimeError("interface is closed")

        self.recognizer.on_activation = broken
        with self.assertLogs("src.speech.speech_module", level="ERROR"):
            self.assertTrue(self.check("астра", synth(WORD_A, seed=74)))


class CropTest(unittest.TestCase):
    # one second of audio where every sample is its own index, so the cut can be checked exactly
    audio = np.arange(RATE, dtype=np.int16).tobytes()

    def cut(self, **kwargs):
        return np.frombuffer(crop_to_words(self.audio, **kwargs), dtype=np.int16)

    def test_cut_to_the_word_with_a_margin(self):
        words = [{"start": 5.30, "end": 5.60, "word": "астра"}]
        cut = self.cut(buffer_start_s=5.0, words=words, samplerate=RATE, margin_s=0.1)
        # from 5.30 - 0.1 to 5.60 + 0.1, counted from the start of the audio at 5.0
        self.assertEqual(cut[0], int(0.2 * RATE))
        self.assertEqual(len(cut), int(0.5 * RATE))

    def test_several_words_are_cut_as_one(self):
        words = [{"start": 0.2, "end": 0.4}, {"start": 0.5, "end": 0.7}]
        cut = self.cut(buffer_start_s=0.0, words=words, samplerate=RATE, margin_s=0.0)
        self.assertEqual((cut[0], len(cut)), (int(0.2 * RATE), int(0.5 * RATE)))

    def test_time_outside_of_the_audio_keeps_everything(self):
        words = [{"start": 50.0, "end": 50.5}]
        self.assertEqual(len(self.cut(buffer_start_s=0.0, words=words, samplerate=RATE)), RATE)

    def test_no_words_keep_everything(self):
        self.assertEqual(crop_to_words(self.audio, 0.0, [], RATE), self.audio)

    def test_the_cut_never_goes_before_the_start(self):
        words = [{"start": 0.0, "end": 0.3}]
        self.assertEqual(self.cut(buffer_start_s=0.0, words=words, samplerate=RATE, margin_s=0.5)[0], 0)


if __name__ == "__main__":
    unittest.main()
