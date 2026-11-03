"""Turns a recording that came from outside (a voice message of Telegram) into what Vosk accepts: 16-bit mono PCM.

A voice message of Telegram is an OGG file with Opus inside. libsndfile (the soundfile package) reads it, other
formats it knows (WAV, FLAC, MP3...) work too. The sound is mixed down to one channel and brought to 16 kHz with numpy,
so there is no need for ffmpeg or another program next to the assistant.
"""
import io
from typing import NamedTuple

import numpy as np

TARGET_RATE = 16000
# A longer message is refused: recognizing it would keep the assistant busy for too long
MAX_SECONDS = 120.0
# A file bigger than this is not a voice message (the bots of Telegram cannot download more than 20 MB anyway)
MAX_BYTES = 25 * 1024 * 1024
# How many frames are read at once, so a huge file is never held in memory in several copies
_BLOCK = 1 << 18


class AudioDecodeError(Exception):
    """The recording cannot be used; the text is written for the user."""


class Decoded(NamedTuple):
    pcm: bytes  # 16-bit little-endian mono
    rate: int
    seconds: float


def _kernel(cutoff, taps):
    """A low-pass filter: cutoff is a part of the sampling rate (0 to 0.5), the window is Kaiser (about 80 dB)."""
    n = np.arange(taps) - (taps - 1) / 2
    kernel = 2 * cutoff * np.sinc(2 * cutoff * n) * np.kaiser(taps, 8.0)
    return kernel / kernel.sum()


def resample(samples, source_rate, target_rate):
    """The samples (float32, one channel) at another sampling rate. Going down, the frequencies the new rate cannot
    hold are cut off first, otherwise they would turn into noise inside the speech."""
    samples = np.asarray(samples, dtype=np.float64)
    if source_rate == target_rate or samples.size == 0:
        return samples.astype(np.float32)
    if target_rate < source_rate:
        taps = (int(round(16 * source_rate / target_rate)) | 1)
        kernel = _kernel(0.45 * target_rate / source_rate, taps)
        half = (taps - 1) // 2
        # "full" and a cut: the "same" mode of numpy returns more than it got when the sound is shorter than the filter
        samples = np.convolve(samples, kernel, mode="full")[half:half + samples.size]
    if source_rate % target_rate == 0:
        return samples[::source_rate // target_rate].astype(np.float32)
    length = max(1, int(samples.size * target_rate / source_rate))
    positions = np.arange(length) * (source_rate / target_rate)
    return np.interp(positions, np.arange(samples.size), samples).astype(np.float32)


def decode_audio(data, max_seconds=MAX_SECONDS, target_rate=TARGET_RATE):
    """The recording (the bytes of a file) as 16-bit mono PCM of the given rate. Raises AudioDecodeError."""
    if not data:
        raise AudioDecodeError("Пустая запись.")
    if len(data) > MAX_BYTES:
        raise AudioDecodeError("Файл слишком большой для голосового сообщения.")
    try:
        import soundfile
    except Exception:
        raise AudioDecodeError("Не установлена библиотека soundfile для чтения голосовых. "
                               "Запустите Astra.bat ещё раз: он доустановит недостающее.") from None
    try:
        with soundfile.SoundFile(io.BytesIO(data)) as file:
            rate = int(file.samplerate)
            if rate <= 0 or file.channels <= 0:
                raise AudioDecodeError("В записи нет звука.")
            limit = int(max_seconds * rate)
            if file.frames > limit:
                raise AudioDecodeError(f"Запись слишком длинная: можно не больше {int(max_seconds)} с.")
            blocks, total = [], 0
            while True:
                block = file.read(_BLOCK, dtype="float32", always_2d=True)
                if not len(block):
                    break
                total += len(block)
                if total > limit:
                    # the header promised less than the file really holds
                    raise AudioDecodeError(f"Запись слишком длинная: можно не больше {int(max_seconds)} с.")
                blocks.append(block.mean(axis=1) if block.shape[1] > 1 else block[:, 0])
    except AudioDecodeError:
        raise
    except Exception:
        raise AudioDecodeError("Не удалось прочитать запись: файл повреждён или такой формат не поддерживается."
                               ) from None
    if not blocks:
        raise AudioDecodeError("В записи нет звука.")
    mono = resample(np.concatenate(blocks), rate, target_rate)
    pcm = np.clip(np.rint(mono * 32767.0), -32768, 32767).astype("<i2").tobytes()
    return Decoded(pcm, target_rate, len(mono) / target_rate)
