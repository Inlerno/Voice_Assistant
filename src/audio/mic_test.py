import json
import logging
import math
import queue
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import sounddevice as sd
import vosk

logger = logging.getLogger(__name__)

BLOCK_SECONDS = 0.1
FULL_SCALE = 32768.0
# Level meter range: DB_FLOOR dBFS is an empty meter, 0 dBFS is a full one
DB_FLOOR = -60.0
# Peak below this means there is no signal at all (muted, unplugged or the wrong device)
SILENCE_PEAK_DB = -50.0
# Peak below this means the voice is too quiet for reliable recognition
QUIET_PEAK_DB = -24.0
# Samples at or above this value are counted as clipped
CLIP_SAMPLE = 32000
# Share of clipped samples above which the sound is considered distorted
CLIP_SHARE_LIMIT = 0.001


# Result of a microphone check: status is ok, quiet, silent, loud, error or cancelled
@dataclass
class MicCheckResult:
    status: str
    message: str
    peak_db: float = -100.0
    text: Optional[str] = None


# Level of a block of 16-bit samples in dBFS (0 is the loudest possible signal)
def block_level_db(samples: np.ndarray) -> float:
    if samples.size == 0:
        return -100.0
    rms = math.sqrt(float(np.mean(np.square(samples.astype(np.float64)))))
    if rms < 1.0:
        return -100.0
    return 20.0 * math.log10(rms / FULL_SCALE)


# Convert dBFS to the 0..1 position of the level meter
def db_to_meter(db: float) -> float:
    return min(1.0, max(0.0, (db - DB_FLOOR) / -DB_FLOOR))


# Decide whether the recorded signal is good, from the loudest sample and the share of clipped samples
def evaluate_signal(peak_db: float, clipped_share: float) -> MicCheckResult:
    if peak_db < SILENCE_PEAK_DB:
        return MicCheckResult(
            "silent",
            "Сигнала нет. Проверьте, что микрофон включён и выбран нужный.",
            peak_db,
        )
    if clipped_share > CLIP_SHARE_LIMIT:
        return MicCheckResult(
            "loud",
            "Слишком громко, звук искажается. Уменьшите усиление микрофона в Windows.",
            peak_db,
        )
    if peak_db < QUIET_PEAK_DB:
        return MicCheckResult(
            "quiet",
            "Слишком тихо. Говорите ближе или увеличьте усиление микрофона в Windows.",
            peak_db,
        )
    return MicCheckResult("ok", "Микрофон работает хорошо.", peak_db)


# Record a few seconds from the device, report the live level and check the signal and the recognition.
#   device     - input device index (None for the system default)
#   duration   - length of the test in seconds
#   model      - loaded Vosk model; when given, the recorded speech is recognized too
#   on_level   - called with the meter position (0..1) for every block, from the calling thread
#   stop_event - threading.Event, the test stops early when it is set
def check_microphone(device, duration=5.0, model=None, on_level: Optional[Callable[[float], None]] = None,
                     stop_event=None) -> MicCheckResult:
    try:
        samplerate = int(sd.query_devices(device, 'input')['default_samplerate'])
        recognizer = vosk.KaldiRecognizer(model, samplerate) if model is not None else None
    except Exception as e:
        logger.error("Mic check: cannot open device %s: %s", device, e)
        return MicCheckResult("error", f"Не удалось открыть микрофон: {e}")

    audio = queue.Queue()
    peak = 0
    clipped = 0
    total = 0
    blocks_needed = round(duration / BLOCK_SECONDS)
    blocks_done = 0
    stopped = False

    def stream_callback(indata, frames, time_info, status):
        if status:
            logger.warning("Mic check stream status: %s", status)
        audio.put(bytes(indata))

    try:
        with sd.RawInputStream(
            samplerate=samplerate,
            blocksize=int(samplerate * BLOCK_SECONDS),
            device=device,
            dtype='int16',
            channels=1,
            callback=stream_callback
        ):
            while blocks_done < blocks_needed:
                if stop_event is not None and stop_event.is_set():
                    stopped = True
                    break
                try:
                    data = audio.get(timeout=1)
                except queue.Empty:
                    break
                blocks_done += 1
                samples = np.frombuffer(data, dtype=np.int16)
                if samples.size == 0:
                    continue
                total += samples.size
                magnitude = np.abs(samples.astype(np.int32))
                peak = max(peak, int(magnitude.max()))
                clipped += int(np.count_nonzero(magnitude >= CLIP_SAMPLE))
                if on_level:
                    on_level(db_to_meter(block_level_db(samples)))
                if recognizer is not None:
                    recognizer.AcceptWaveform(data)
    except Exception as e:
        logger.error("Mic check failed on device %s: %s", device, e)
        return MicCheckResult("error", f"Не удалось открыть микрофон: {e}. Возможно, он занят другой программой.")

    if stopped:
        return MicCheckResult("cancelled", "Проверка остановлена.")
    if total == 0:
        return MicCheckResult("error", "Микрофон не передаёт звук. Попробуйте выбрать другое устройство.")

    peak_db = 20.0 * math.log10(peak / FULL_SCALE) if peak > 0 else -100.0
    result = evaluate_signal(peak_db, clipped / total)
    if recognizer is not None:
        try:
            result.text = json.loads(recognizer.FinalResult()).get('text', '').strip() or None
        except Exception as e:
            logger.error("Mic check recognition failed: %s", e)
    logger.info("Mic check: %s, peak %.1f dBFS, text: %s", result.status, peak_db, result.text)
    return result
