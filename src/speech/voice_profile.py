import json
import logging
import math
import threading
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)

PROFILE_PATH = Path(__file__).resolve().parents[1] / "config" / "voice_profile.json"

TARGET_RATE = 16000
FRAME_LENGTH = 400   # 25 ms
FRAME_HOP = 160      # 10 ms
FFT_SIZE = 512
MEL_FILTERS = 18
MFCC_COUNT = 12      # coefficients 1..12, the 0th one only carries loudness
MEL_LOW_HZ = 100.0
MEL_HIGH_HZ = 4000.0
# Part of the power that is always kept when the noise is subtracted
NOISE_FLOOR_FRACTION = 0.3
# Divide the features by their spread, not only subtract the mean
NORMALIZE_VARIANCE = True

# A phrase this long (after cutting the silence) can not be the wake word
MIN_WORD_SECONDS = 0.2
MAX_WORD_SECONDS = 1.6
# Frames quieter than the loudest one by this many dB are treated as silence
SILENCE_DB = 30.0
# The loudest frame must be above this level, otherwise there is nothing but noise
MIN_PEAK_DB = -55.0
# Speech must be louder than the background noise by at least this many dB
NOISE_MARGIN_DB = 9.0

# Number of closest templates that are averaged into the score of a phrase
NEAREST_TEMPLATES = 3
# Threshold = the worst score among the enrolled samples multiplied by this margin
THRESHOLD_MARGIN = 1.35
MAX_TEMPLATES = 16
# A phrase is learned from only when its score is below this part of the threshold
ADAPT_CONFIDENCE = 0.8
# How much of a freshly calculated threshold is mixed into the stored one after learning
THRESHOLD_BLEND = 0.3
# Learning can not make the threshold higher than the one set at enrollment multiplied by this value,
# otherwise the profile would slowly start to accept other words and voices
THRESHOLD_DRIFT_LIMIT = 1.25
# Every recorded sample also gets a copy with this much noise (signal to noise ratio, dB),
# so the profile also knows how the word sounds in a not perfectly quiet room
AUGMENT_SNR_DB = 25.0
# The user can make the check stricter (below 1) or softer (above 1) by scaling the threshold
SENSITIVITY_MIN = 0.7
SENSITIVITY_MAX = 1.5


# ---------- Features ----------

def _hz_to_mel(hz):
    return 2595.0 * np.log10(1.0 + hz / 700.0)


def _mel_to_hz(mel):
    return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)


@lru_cache(maxsize=1)
def _mel_filterbank():
    points = _mel_to_hz(np.linspace(_hz_to_mel(MEL_LOW_HZ), _hz_to_mel(MEL_HIGH_HZ), MEL_FILTERS + 2))
    bins = np.floor((FFT_SIZE + 1) * points / TARGET_RATE).astype(int)
    bank = np.zeros((MEL_FILTERS, FFT_SIZE // 2 + 1))
    for i in range(MEL_FILTERS):
        left, center, right = bins[i], bins[i + 1], bins[i + 2]
        for k in range(left, center):
            bank[i, k] = (k - left) / max(center - left, 1)
        for k in range(center, right):
            bank[i, k] = (right - k) / max(right - center, 1)
    return bank


@lru_cache(maxsize=1)
def _dct_matrix():
    n = np.arange(MEL_FILTERS)
    k = np.arange(MFCC_COUNT + 1)[:, None]
    return np.sqrt(2.0 / MEL_FILTERS) * np.cos(np.pi * k * (2 * n + 1) / (2 * MEL_FILTERS))


# 16-bit PCM bytes from the microphone to float samples in -1..1 at 16 kHz
def to_float_16k(pcm: bytes, samplerate: int) -> np.ndarray:
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if samplerate == TARGET_RATE or samples.size == 0:
        return samples
    factor = samplerate / TARGET_RATE
    window = int(round(factor))
    if window >= 2:
        # Cheap anti-aliasing before the sample rate is lowered
        samples = np.convolve(samples, np.ones(window, dtype=np.float32) / window, mode="same")
    positions = np.arange(0, samples.size - 1, factor)
    return np.interp(positions, np.arange(samples.size), samples).astype(np.float32)


def _frame_energy_db(samples: np.ndarray) -> np.ndarray:
    count = 1 + max(0, samples.size - FRAME_LENGTH) // FRAME_HOP
    energy = np.empty(count)
    for i in range(count):
        frame = samples[i * FRAME_HOP:i * FRAME_HOP + FRAME_LENGTH]
        energy[i] = 10.0 * math.log10(float(np.mean(np.square(frame))) + 1e-12) if frame.size else -120.0
    return energy


# Frame range of the speech inside the audio, or None when there is no speech
def speech_bounds(samples: np.ndarray):
    if samples.size < FRAME_LENGTH:
        return None
    energy = _frame_energy_db(samples)
    peak = float(energy.max())
    if peak < MIN_PEAK_DB:
        return None
    # In a noisy room the threshold has to rise above the noise, which is estimated from the quietest frames
    noise_floor = float(np.percentile(energy, 10))
    active = np.flatnonzero(energy > max(peak - SILENCE_DB, noise_floor + NOISE_MARGIN_DB))
    if active.size == 0:
        return None
    return max(0, int(active[0]) - 3), int(active[-1]) + 3, energy


def _power_spectrum(samples: np.ndarray) -> np.ndarray:
    emphasized = np.append(samples[0], samples[1:] - 0.97 * samples[:-1])
    count = 1 + (emphasized.size - FRAME_LENGTH) // FRAME_HOP
    indices = np.arange(FRAME_LENGTH)[None, :] + FRAME_HOP * np.arange(count)[:, None]
    frames = emphasized[indices] * np.hamming(FRAME_LENGTH)
    return np.abs(np.fft.rfft(frames, FFT_SIZE)) ** 2


# MFCC features from the power spectrum of a short phrase, shape (frames, MFCC_COUNT), normalized per phrase.
# noise is the average power spectrum of the background; it is subtracted so that noise changes the features less.
def extract_features(power: np.ndarray, noise: Optional[np.ndarray] = None) -> Optional[np.ndarray]:
    if power.shape[0] < 3:
        return None
    if noise is not None:
        power = np.maximum(power - noise, NOISE_FLOOR_FRACTION * power)
    mel = np.log(power @ _mel_filterbank().T + 1e-10)
    cepstrum = (mel @ _dct_matrix().T)[:, 1:MFCC_COUNT + 1]
    # Removing the mean and the scale makes the features less dependent on the microphone and the volume
    centered = cepstrum - cepstrum.mean(axis=0)
    return centered / (cepstrum.std(axis=0) + 1e-6) if NORMALIZE_VARIANCE else centered


# Features of the wake word from raw microphone audio, or None if it does not look like a short word
def word_features(pcm: bytes, samplerate: int) -> Optional[np.ndarray]:
    samples = to_float_16k(pcm, samplerate)
    bounds = speech_bounds(samples)
    if bounds is None:
        return None
    first, last, energy = bounds
    start, end = first * FRAME_HOP, last * FRAME_HOP + FRAME_LENGTH
    seconds = (end - start) / TARGET_RATE
    if seconds < MIN_WORD_SECONDS or seconds > MAX_WORD_SECONDS:
        return None
    power = _power_spectrum(samples)
    # Background noise: the frames outside of the speech, or the quietest ones if there is little silence
    outside = np.concatenate([np.arange(0, max(first - 2, 0)), np.arange(min(last + 3, len(energy)), len(energy))])
    if outside.size < 3:
        outside = np.argsort(energy)[:max(3, len(energy) // 10)]
    noise = power[outside.astype(int)].mean(axis=0)
    return extract_features(power[first:last + 1], noise)


# The same recording with white noise added over the whole stream
def add_noise(pcm: bytes, snr_db: float, seed: int = 0) -> bytes:
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
    bounds = speech_bounds(to_float_16k(pcm, TARGET_RATE))
    if bounds is None or samples.size == 0:
        return pcm
    # The level of the speech itself is measured on the loud part only, the silence must not lower it
    loud = samples[bounds[0] * FRAME_HOP:bounds[1] * FRAME_HOP + FRAME_LENGTH]
    rms = math.sqrt(float(np.mean(np.square(loud)))) if loud.size else 0.0
    noise = np.random.default_rng(seed).normal(0.0, rms / (10.0 ** (snr_db / 20.0)), samples.size)
    return np.clip(samples + noise, -32768, 32767).astype(np.int16).tobytes()


# Features of a recorded sample for the profile: the clean one and a noisy copy.
# The first item is None when the recording does not look like a short word.
def sample_features(pcm: bytes, samplerate: int, seed: int = 0) -> List[Optional[np.ndarray]]:
    if samplerate != TARGET_RATE:
        pcm = (to_float_16k(pcm, samplerate) * 32767.0).astype(np.int16).tobytes()
    clean = word_features(pcm, TARGET_RATE)
    if clean is None:
        return [None]
    noisy = word_features(add_noise(pcm, AUGMENT_SNR_DB, seed), TARGET_RATE)
    return [clean] + ([noisy] if noisy is not None else [])


# ---------- Comparison ----------

# Dynamic time warping distance between two feature sequences, lower means more similar
def dtw_distance(a: np.ndarray, b: np.ndarray, band: float = 0.35) -> float:
    n, m = len(a), len(b)
    if n == 0 or m == 0 or not (0.5 <= n / m <= 2.0):
        return math.inf
    cost = np.sqrt(((a[:, None, :] - b[None, :, :]) ** 2).sum(axis=2))
    width = max(int(band * max(n, m)), abs(n - m) + 1)
    total = np.full((n + 1, m + 1), math.inf)
    total[0, 0] = 0.0
    for i in range(1, n + 1):
        low, high = max(1, i - width), min(m, i + width)
        for j in range(low, high + 1):
            total[i, j] = cost[i - 1, j - 1] + min(total[i - 1, j], total[i, j - 1], total[i - 1, j - 1])
    return float(total[n, m] / (n + m))


def _nearest_mean(distances: List[float]) -> float:
    finite = sorted(d for d in distances if math.isfinite(d))
    if not finite:
        return math.inf
    return float(np.mean(finite[:NEAREST_TEMPLATES]))


# Voice profile of the owner: how the wake word sounds when the owner says it
class VoiceProfile:
    def __init__(self, path: Path = PROFILE_PATH):
        self.path = Path(path)
        self.word = ""
        self.templates: List[np.ndarray] = []
        self.threshold = 0.0
        # The threshold right after enrollment, learning may move the real one only a little away from it
        self.base_threshold = 0.0
        self.sensitivity = 1.0
        self.created = None
        self.updated = None
        self.adaptations = 0
        # Saving can be requested by the interface and by the assistant thread at the same time
        self._save_lock = threading.Lock()
        # The templates are compared on the assistant thread and updated by the learning thread
        self._lock = threading.RLock()

    @property
    def enrolled(self) -> bool:
        return len(self.templates) >= 2 and self.threshold > 0

    # Score of a phrase: the distance to the closest templates, lower is better
    def score(self, features: np.ndarray) -> float:
        with self._lock:
            templates = list(self.templates)
        return _nearest_mean([dtw_distance(features, t) for t in templates])

    # The threshold the score is compared with, including the sensitivity chosen by the user
    @property
    def active_threshold(self) -> float:
        return self.threshold * self.sensitivity

    def set_sensitivity(self, value: float):
        self.sensitivity = min(SENSITIVITY_MAX, max(SENSITIVITY_MIN, float(value)))

    def accepts(self, score: float) -> bool:
        return score <= self.active_threshold

    # Scores of every template against all the others
    def _leave_one_out(self, templates: List[np.ndarray]) -> List[float]:
        scores = []
        for i, template in enumerate(templates):
            others = [dtw_distance(template, t) for j, t in enumerate(templates) if j != i]
            scores.append(_nearest_mean(others))
        return scores

    def _calculated_threshold(self, templates: List[np.ndarray]) -> float:
        finite = [s for s in self._leave_one_out(templates) if math.isfinite(s)]
        return max(finite) * THRESHOLD_MARGIN if finite else 0.0

    # Replace the profile with freshly recorded samples; returns the leave-one-out scores
    def enroll(self, word: str, samples: List[np.ndarray]) -> List[float]:
        templates = list(samples)
        scores = self._leave_one_out(templates)
        threshold = self._calculated_threshold(templates)
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            self.word = word
            self.templates = templates
            self.threshold = self.base_threshold = threshold
            self.created = self.updated = now
            self.adaptations = 0
        return scores

    # Whether a phrase with this score is confident enough to learn from
    def would_adapt(self, score: float) -> bool:
        return self.enrolled and math.isfinite(score) and score <= self.threshold * ADAPT_CONFIDENCE

    # Keep learning: a confidently recognized phrase becomes a new template and the threshold is adjusted.
    # Returns True if the profile was changed.
    def adapt(self, features: np.ndarray, score: float) -> bool:
        if not self.would_adapt(score):
            return False
        with self._lock:
            templates = self.templates + [features]
            if len(templates) > MAX_TEMPLATES:
                # Drop the template that is the least similar to the others
                outlier = max(
                    range(len(templates)),
                    key=lambda i: _nearest_mean(
                        [dtw_distance(templates[i], t) for j, t in enumerate(templates) if j != i]
                    ),
                )
                del templates[outlier]
            calculated = self._calculated_threshold(templates)
            self.templates = templates
            if calculated > 0:
                blended = (1 - THRESHOLD_BLEND) * self.threshold + THRESHOLD_BLEND * calculated
                limit = self.base_threshold * THRESHOLD_DRIFT_LIMIT if self.base_threshold > 0 else math.inf
                self.threshold = min(blended, limit)
            self.adaptations += 1
            self.updated = datetime.now().isoformat(timespec="seconds")
        return True

    def reset(self):
        with self._lock:
            self.word = ""
            self.templates = []
            self.threshold = self.base_threshold = 0.0
            self.sensitivity = 1.0
            self.created = self.updated = None
            self.adaptations = 0
        with self._save_lock:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass

    def save(self):
        with self._lock:
            data = {
                "version": 1,
                "word": self.word,
                "created": self.created,
                "updated": self.updated,
                "adaptations": self.adaptations,
                "threshold": round(self.threshold, 5),
                "base_threshold": round(self.base_threshold, 5),
                "sensitivity": round(self.sensitivity, 3),
                "templates": [np.round(t, 4).tolist() for t in self.templates],
            }
        with self._save_lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Write to a temporary file first so a crash can not leave a half-written profile
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.path)

    @classmethod
    def load(cls, path: Path = PROFILE_PATH) -> "VoiceProfile":
        profile = cls(path)
        try:
            data = json.loads(profile.path.read_text(encoding="utf-8"))
            profile.word = data.get("word", "")
            profile.templates = [np.array(t, dtype=np.float64) for t in data.get("templates", [])]
            profile.threshold = float(data.get("threshold", 0.0))
            profile.base_threshold = float(data.get("base_threshold", profile.threshold))
            profile.set_sensitivity(data.get("sensitivity", 1.0))
            profile.created = data.get("created")
            profile.updated = data.get("updated")
            profile.adaptations = int(data.get("adaptations", 0))
        except FileNotFoundError:
            pass
        except Exception:
            logger.exception("Не удалось прочитать профиль голоса, он будет создан заново")
            profile.reset()
        return profile

    # Short human readable state for the interface
    def summary(self) -> str:
        if not self.enrolled:
            return "Профиль не создан: ассистент реагирует на любой голос."
        text = f"Профиль создан: образцов {len(self.templates)}"
        if self.adaptations:
            text += f", подстроек по ходу работы {self.adaptations}"
        return text + "."
