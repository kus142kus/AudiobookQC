"""Second-pass evidence filtering. Suppresses obvious ASR hallucinations.

A second decoding with the same acoustic model is correlated, *not* independent
proof that the narrator made a mistake. Output must be labelled a candidate.
"""
from __future__ import annotations
import re
import tempfile
from pathlib import Path
from .analysis import normalize, tokenize, effective_severity


MAX_RECHECKS_PER_PART = 35


def phrase_in(phrase: str, transcript: str) -> bool:
    needle = [normalize(w) for w in tokenize(phrase)]
    haystack = [normalize(w) for w in tokenize(transcript)]
    if not needle or not haystack:
        return False
    # Do not allow a match across distant points in the recording.
    return any(haystack[i:i+len(needle)] == needle for i in range(len(haystack)-len(needle)+1))


def classify_second_pass(issue: dict, alternative_text: str) -> dict:
    """Decide whether machine disagreement survives a different audio crop."""
    out = dict(issue)
    if issue.get('kind') not in ('replacement', 'omission', 'extra'):
        return out
    expected = issue.get('expected', '')
    spoken = issue.get('spoken', '')
    expected_seen = phrase_in(expected, alternative_text)
    spoken_seen = phrase_in(spoken, alternative_text)
    out['rechecked'] = True
    if expected_seen:
        out['severity'] = 'review'
        out['confidence'] = 'low'
        out['reason'] = 'Повторное распознавание услышало исходный текст; вероятна ошибка первой расшифровки. Только полный журнал.'
    elif spoken_seen and not expected_seen and effective_severity(issue) == 'high':
        out['severity'] = 'high'
        out['confidence'] = 'medium'
        out['reason'] = 'Два прохода распознавания обнаружили расхождение. Это не доказательство ошибки: проверьте звучание.'
    else:
        out['severity'] = 'review'
        out['confidence'] = 'low'
        out['reason'] = 'Повторное распознавание не подтвердило различие. Проверьте при необходимости.'
    # Retain the original deterministic issue ID; existing review markings remain valid.
    return out


def prioritize_rechecks(issues: list[dict], budget: int = MAX_RECHECKS_PER_PART) -> list[int]:
    candidates = []
    for i, x in enumerate(issues):
        if x.get('kind') not in ('replacement', 'omission', 'extra') or effective_severity(x) != 'high':
            continue
        size = len(tokenize(x.get('expected', ''))) + len(tokenize(x.get('spoken', '')))
        # Long phrases and consequential negations are stronger candidates.
        score = size + (8 if x['kind'] == 'omission' else 0)
        candidates.append((score, i))
    # Prioritise by importance, then return in temporal order to minimise seek overhead.
    return sorted(i for _, i in sorted(candidates, reverse=True)[:budget])


def recheck_issues(model, audio: Path, issues: list[dict], duration: float,
                   cut_audio, *, decoder=None, should_stop=None) -> list[dict]:
    """Re-decode only significant candidates in shifted, wider 15+ second windows."""
    result = [dict(x) for x in issues]
    target = prioritize_rechecks(result)
    for i in target:
        if should_stop is not None and should_stop():
            break
        item = result[i]
        centre = max(0.0, float(item['start']))
        start = max(0.0, centre - 8.0)
        end = min(float(duration), max(centre + 12.0, float(item['end']) + 8.0))
        if end <= start + 0.5:
            continue
        try:
            with tempfile.TemporaryDirectory(prefix='aqc-verify-') as tmp:
                clip = Path(tmp) / 'verify.wav'
                cut_audio(audio, start, end - start, clip)
                if decoder:
                    alt = decoder(model, clip)
                else:
                    segments, _ = model.transcribe(str(clip), language='ru', beam_size=8,
                        vad_filter=False, word_timestamps=False, condition_on_previous_text=False,
                        temperature=0)
                    alt = ' '.join(x.text for x in segments)
            result[i] = classify_second_pass(item, alt)
        except Exception as exc:
            # Never mark a failed verification as success or erase the candidate.
            result[i]['severity'] = 'review'
            result[i]['confidence'] = 'low'
            result[i]['reason'] = f'Второй проход не завершился ({type(exc).__name__}); требуется ручная проверка.'
    return result
