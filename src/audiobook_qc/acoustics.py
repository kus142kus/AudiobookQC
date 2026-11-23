"""Conservative, bounded-memory PCM quality checks.

These detectors locate suspicious *signals*, not proof of a recording defect.
Only high-confidence clipping gets the default high priority; all other
findings require listening and appear as review candidates.
"""
from __future__ import annotations

import hashlib
import wave
from pathlib import Path


def _issue(kind: str, start: float, end: float, reason: str,
           severity: str = 'review', confidence: str = 'low') -> dict:
    issue = {'kind': kind, 'severity': severity, 'confidence': confidence,
             'start': round(start, 2), 'end': round(end, 2),
             'spoken': '—', 'expected': '—', 'reason': reason}
    issue['id'] = hashlib.sha256(f'{kind}|{start:.2f}|{end:.2f}'.encode()).hexdigest()[:14]
    return issue


def _runs(mask):
    import numpy as np
    changes = np.diff(np.r_[False, mask, False].astype('int8'))
    return zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1))


def inspect_wav(path: Path, absolute_start: float, inner_start: float, inner_end: float) -> list[dict]:
    """Analyze a single ffmpeg-normalized WAV chunk (16k PCM mono) in RAM.

    Absolute times are always restricted to [inner_start, inner_end) so that
    overlapping chunks do not yield duplicate findings.
    """
    import numpy as np
    with wave.open(str(path), 'rb') as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getframerate() <= 0:
            return []
        sr = w.getframerate()
        a = max(0, int(round((inner_start - absolute_start) * sr)))
        b = min(w.getnframes(), int(round((inner_end - absolute_start) * sr)))
        w.setpos(min(a, w.getnframes()))
        pcm = np.frombuffer(w.readframes(max(0, b-a)), dtype='<i2').astype(np.float32)
    if pcm.size < sr // 2:
        return []
    x = pcm / 32768.0
    found = []
    near = np.abs(pcm) >= 32600
    if near.sum() >= max(20, int(x.size * .0025)):
        long = [i for i, j in _runs(near) if j-i >= max(16, int(sr*.002))]
        if long:
            t = inner_start + long[0]/sr
            found.append(_issue('clipping', t, min(inner_end,t+2),
                'Продолжительные отсчёты у предела цифровой шкалы: возможная перегрузка записи. Прослушайте.',
                severity='high', confidence='medium'))
    # Isolated full-scale spikes are *candidates* for microphone/cable clicks.
    # Require a very short impulse with quieter samples on both sides; speech
    # plosives may still resemble clicks, hence review-only classification.
    if x.size > 100:
        i = np.flatnonzero((np.abs(x[2:-2]) > .78) &
                           (np.abs(x[:-4]) < .20) & (np.abs(x[4:]) < .20)) + 2
        clusters = []
        for idx in i:
            if not clusters or idx - clusters[-1][-1] > int(sr*.3):
                clusters.append([int(idx)])
            elif len(clusters[-1]) < 12:
                clusters[-1].append(int(idx))
        for g in clusters[:12]:
            t = inner_start + g[0]/sr
            found.append(_issue('click', max(inner_start,t-.05), min(inner_end,t+.12),
                'Короткий резкий импульс: возможный щелчок. Это также может быть согласный звук речи.'))
    # FFT only over one-second windows; bounded even for multi-hour projects.
    n = int(sr)
    if x.size < 3*n:
        return found
    windows = x[:(x.size//n)*n].reshape(-1,n)
    rms = np.sqrt(np.mean(windows**2, axis=1))
    # Quiet windows only, not spoken vowels. Silence never counts as hum/noise.
    quiet = np.flatnonzero((rms >= .0015) & (rms <= .045))
    if quiet.size >= 4:
        f = np.fft.rfftfreq(n,1/sr)
        from numpy.fft import rfft
        hum_votes, hiss_votes = [], []
        for wi in quiet[:240]:
            frame = windows[wi] * np.hanning(n)
            power = np.abs(rfft(frame))**2
            total = float(power[(f >= 20) & (f <= 7800)].sum()) + 1e-9
            for mains in (50,60):
                ix = int(np.argmin(np.abs(f-mains)))
                # Local narrowband contrast, plus absolute spectral proportion.
                peak = float(power[ix-1:ix+2].sum())
                baseline = float(power[max(0,ix-9):ix+10].sum()) + 1e-9
                if peak/total > .22 and peak/baseline > .58:
                    hum_votes.append((int(wi),mains))
                    break
            high = float(power[(f>=3800)&(f<7600)].sum())
            low = float(power[(f>=150)&(f<3400)].sum()) + 1e-9
            if high / low > .55 and high/total > .30:
                hiss_votes.append(int(wi))
        if len(hum_votes) >= 3 and len(hum_votes)/len(quiet) >= .45:
            mains = max((50,60), key=lambda p:sum(x==p for _,x in hum_votes))
            t = inner_start + hum_votes[0][0]
            found.append(_issue('hum', t, min(inner_end,t+2),
                 f'В тихих промежутках обнаружена узкополосная составляющая около {mains} Гц: возможный сетевой гул.'))
        if len(hiss_votes) >= 4 and len(hiss_votes)/len(quiet) >= .55:
            t = inner_start + hiss_votes[0]
            found.append(_issue('hiss',t,min(inner_end,t+2),
                 'В паузах высокая доля высокочастотной энергии: возможное шипение или шум.'))
    # Persistent unexpected level jump only, avoid flagging natural syllables.
    if len(rms) >= 6:
        for wi in range(2,len(rms)-2):
            flank = float(np.median(np.r_[rms[wi-2:wi],rms[wi+1:wi+3]]))
            if flank > .004 and rms[wi] > .22 and rms[wi] > flank*9:
                t=inner_start+wi
                found.append(_issue('level_jump',t,min(inner_end,t+1),
                    'Заметный кратковременный скачок уровня. Проверьте, не было ли удара или помехи.'))
                break
    return sorted(found,key=lambda d:(d['start'],d['kind']))[:30]
