"""Conservative PCM technical checks, separate from ASR conclusions.

These are *candidates*, not a general sound-event classifier. In particular,
we cannot certify clicks, coughs, phone sounds, or incorrect stress here.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import wave


def inspect_wav(path: Path, absolute_start: float, inner_start: float, inner_end: float) -> list[dict]:
    """Return high-confidence clipping candidates within the owned time window.

    Uses short, consecutive near-full-scale intervals rather than one full-scale
    sample (which would generate false positives on ordinary speech transients).
    WAV is decoded by ffmpeg to 16 kHz mono 16-bit before calling this function.
    """
    import numpy as np
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            return []
        sr = w.getframerate()
        if sr <= 0:
            return []
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    if len(data) < sr:
        return []
    start_i = max(0, int((inner_start - absolute_start) * sr))
    end_i = min(len(data), int((inner_end - absolute_start) * sr))
    data = data[start_i:end_i]
    if data.size < sr // 2:
        return []
    near = np.abs(data.astype(np.int32)) >= 32600
    # At least 0.25% of samples pinned close to rail and a continuous 2ms run.
    count = int(near.sum())
    if count < max(20, int(data.size * 0.0025)):
        return []
    transitions = np.diff(np.pad(near.astype(np.int8), (1, 1)))
    begin = np.flatnonzero(transitions == 1)
    end = np.flatnonzero(transitions == -1)
    long_runs = end - begin >= max(16, int(sr * 0.002))
    if not np.any(long_runs):
        return []
    first = int(begin[long_runs][0])
    t = round(inner_start + first / sr, 2)
    issue = {
        "kind": "clipping", "severity": "high", "confidence": "medium",
        "start": t, "end": round(min(inner_end, t + 2), 2),
        "spoken": "—", "expected": "—",
        "reason": "Обнаружено продолжительное накопление отсчётов около максимальной амплитуды. Возможно, запись перегружена; прослушайте участок.",
    }
    issue["id"] = hashlib.sha256(f'clipping|{t:.2f}'.encode()).hexdigest()[:14]
    return [issue]
