"""Conservative manuscript/transcript comparison. Findings are REVIEW CANDIDATES.

Whisper is fallible. Never treat text differences as proof of a spoken mistake.
"""
from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher
import hashlib
import re

TOKEN_RE = re.compile(r"[^\W_]+(?:[-’'][^\W_]+)*", re.UNICODE)
IMPORTANT_SHORT = {"не", "ни", "нет", "без", "но", "или", "и", "а", "до", "после"}
# Deliberately small and exact, so we do not match innocent word fragments.
OBSCENITIES = {"блядь", "блять", "нахуй", "пиздец", "ебать", "ёбать", "хуй", "сука"}



def normalize(s: str) -> str:
    return s.casefold().replace("ё", "е").replace("’", "'")


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text)


def fmt_time(seconds: float) -> str:
    sec = max(0, int(seconds))
    return f"{sec // 3600:02}:{(sec % 3600) // 60:02}:{sec % 60:02}"


def match_anchor(book: list[str], spoken: list[str], cursor: int) -> tuple[int, int]:
    """Estimate book offset from exact four-word anchors with monotonic preference.

    Returned (estimated offset, anchor votes). Large text inputs remain manageable:
    only index a limited area after a previous successful alignment.
    """
    if len(spoken) < 4 or len(book) < 4:
        return max(0, cursor), 0
    if cursor <= 0:
        begin, end = 0, len(book)
    else:
        begin, end = max(0, cursor - 130), min(len(book), cursor + max(len(spoken) * 3, 1200))
    lookup = defaultdict(list)
    for i in range(begin, end - 3):
        key = tuple(book[i:i + 4])
        if len(lookup[key]) < 20:
            lookup[key].append(i)
    votes = defaultdict(int)
    for j in range(0, min(len(spoken) - 3, 200), 2):
        key = tuple(spoken[j:j + 4])
        for i in lookup.get(key, []):
            estimate = i - j
            if estimate >= -10 and (cursor <= 0 or estimate >= cursor - 120):
                votes[(estimate // 12) * 12] += 1
    if not votes:
        return max(0, cursor), 0
    best = max(votes, key=lambda x: (votes[x], -(abs(x - cursor) if cursor > 0 else x) / 100))
    return max(0, best), votes[best]


def weak_ending_change(before: list[str], after: list[str]) -> bool:
    if len(before) != 1 or len(after) != 1:
        return False
    a, b = normalize(before[0]), normalize(after[0])
    if a == b:
        return True
    if a in IMPORTANT_SHORT or b in IMPORTANT_SHORT:
        return False
    shared = 0
    for x, y in zip(a, b):
        if x != y:
            break
        shared += 1
    # Suppress very close endings only; do not suppress 'автор/авторы' or
    # 'цивилизованный/цивилизационный' (could change number or meaning).
    if len(a) >= 7 and len(b) >= 7 and shared >= min(len(a), len(b)) - 2 and abs(len(a)-len(b)) <= 1:
        return True
    return False


def recent_repeat(added: list[str], before: list[str], after: list[str]) -> bool:
    if len(added) < 3:
        return False
    phrase = [normalize(x) for x in added]
    left = [normalize(x) for x in before[-50:]]
    right = [normalize(x) for x in after[:50]]
    if len(phrase) >= 4:
        for window in (left, right):
            for i in range(0, len(window) - len(phrase) + 1):
                if window[i:i+len(phrase)] == phrase:
                    return True
    # Short self-corrections may be imperfect: e.g. "он быстрее... он быстро".
    for window in (left, right):
        if not window:
            continue
        for i in range(0, max(1, len(window) - len(phrase) + 1)):
            fragment = window[i:i + len(phrase)]
            if len(fragment) >= 3 and SequenceMatcher(None, phrase, fragment, autojunk=False).ratio() >= 0.82:
                return True
    return False


def detect_unscripted_profanity(book_tokens: list[str], words: list[dict]) -> list[dict]:
    """Review candidates, not definitive identification of a swear word.

    Only exact tokens absent from the entire manuscript are reported. If the
    manuscript contains the word at any location, prefer a manuscript alignment
    finding instead of a global profanity alert (fewer false alarms).
    """
    book_set = set(map(normalize, book_tokens))
    findings = []
    for w in words:
        token = normalize(w["word"])
        if token not in {normalize(x) for x in OBSCENITIES} or token in book_set:
            continue
        item = {
            "kind": "profanity", "severity": "high", "confidence": "low",
            "start": float(w["start"]), "end": float(w["end"]),
            "spoken": w["word"], "expected": "Не найдено в тексте книги",
            "reason": "В расшифровке обнаружено возможное бранное слово вне рукописи. Обязательно проверьте на слух: распознавание может ошибиться.",
        }
        key = f'{item["kind"]}|{item["start"]:.2f}|{item["spoken"]}'
        item["id"] = hashlib.sha256(key.encode("utf-8")).hexdigest()[:14]
        findings.append(item)
    return findings


def find_explicit_repeats(words: list[dict], book_norm: list[str], prev_tail: list[str] | None = None) -> list[dict]:
    """Flag strong near-adjacent verbatim repetitions not already caught by alignment.

    This is intentionally conservative: 5+ words, within 25 words, and the
    repeated expression is absent as a consecutive repeat in the manuscript.
    """
    norm = [normalize(w["word"]) for w in words]
    result = []
    seen_starts = set()
    for second in range(5, len(norm) - 4):
        if second in seen_starts:
            continue
        for distance in range(5, min(26, second + 1)):
            first = second - distance
            match_len = 0
            while (second + match_len < len(norm) and first + match_len < second
                   and norm[first + match_len] == norm[second + match_len]
                   and match_len < 25):
                match_len += 1
            if match_len < 5:
                continue
            # If manuscript legitimately repeats this exact phrase, lower confidence.
            phrase = norm[second:second + match_len]
            repeated_in_source = False
            for j in range(len(book_norm) - len(phrase) * 2 + 1):
                if book_norm[j:j+len(phrase)] == phrase and book_norm[j+len(phrase):j+2*len(phrase)] == phrase:
                    repeated_in_source = True
                    break
            if repeated_in_source:
                continue
            result.append({
                "kind": "repeat", "severity": "high", "confidence": "medium",
                "start": words[second]["start"], "end": words[second+match_len-1]["end"],
                "spoken": " ".join(w["word"] for w in words[first:second+match_len]),
                "expected": "Одно чтение этого фрагмента (проверить по оригиналу)",
                "reason": "Фраза повторена почти подряд. Возможно, первый дубль нужно удалить. Проверьте запись.",
            })
            seen_starts.update(range(second, second+match_len))
            break
    return result


def compare(book_tokens: list[str], words: list[dict], cursor: int = 0, prev_tail_words: list[dict] | None = None) -> tuple[list[dict], int, dict]:
    """Align one completed time-part to the manuscript.

    Word dict has absolute-time start/end and actual word. Cursor is a monotonic
    manuscript token offset, checkpointed after every completed part.
    """
    if not words:
        issue = {
            "kind": "uncertain", "severity": "review", "confidence": "low",
            "start": 0.0, "end": 0.0,
            "spoken": "Речь не распознана", "expected": "Необходимо проверить, есть ли речь в этой части",
            "reason": "Распознавание не вернуло слов. Это не подтверждает отсутствие ошибок: возможно, запись без речи, плохое качество или сбой модели.",
        }
        issue["id"] = hashlib.sha256(f"empty|{cursor}".encode()).hexdigest()[:14]
        return [issue], cursor, {"matched": 0, "note": "Речь не распознана: нужна ручная проверка"}
    spoken = [w["word"] for w in words]
    source_norm = [normalize(w) for w in book_tokens]
    spoken_norm = [normalize(w) for w in spoken]
    offset, votes = match_anchor(source_norm, spoken_norm, cursor)
    start = max(0, offset - 35)
    # Include adequate manuscript headroom for omitted text, but no whole-book O(N²) alignment.
    end = min(len(book_tokens), start + max(len(spoken) * 2 + 160, 400))
    expected = source_norm[start:end]
    sm = SequenceMatcher(None, expected, spoken_norm, autojunk=len(spoken) > 1200)
    blocks = [b for b in sm.get_matching_blocks() if b.size >= 2]
    matched = sum(b.size for b in blocks)
    quality = round(matched / max(1, len(spoken_norm)), 3)
    # No confident anchor or too little overlap: do not hallucinate dozens of typos.
    if not blocks or quality < 0.40:
        uncertain = {
            "kind": "uncertain", "severity": "review", "confidence": "low",
            "start": words[0]["start"], "end": words[-1]["end"],
            "spoken": " ".join(spoken[:15]), "expected": "Не удалось надёжно сопоставить с рукописью",
            "reason": "Недостаточное совпадение распознавания и текста. Проверьте соответствие файлов или качество речи.",
        }
        uncertain["id"] = hashlib.sha256(("uncertain|" + str(uncertain["start"]) + "|" + uncertain["spoken"]).encode("utf-8")).hexdigest()[:14]
        return [uncertain], cursor, {"matched": quality, "votes": votes, "note": "Низкое качество выравнивания"}
    first, last = blocks[0], blocks[-1]
    lower_a, upper_a = first.a, last.a + last.size
    lower_b, upper_b = first.b, last.b + last.size
    issues = []
    # A failed alignment at the edge should be visible rather than silently lost;
    # small unmatched edges are intentionally ignored (usual ASR word boundaries).
    for edge_start, edge_end, edge_name in ((0, lower_b, "начале"), (upper_b, len(words), "конце")):
        if edge_end - edge_start >= 8:
            fragment = words[edge_start:edge_end]
            issues.append({
                "kind": "uncertain", "severity": "review", "confidence": "low",
                "start": float(fragment[0]["start"]), "end": float(fragment[-1]["end"]),
                "spoken": " ".join(w["word"] for w in fragment[:30]),
                "expected": "Нужна ручная сверка с рукописью",
                "reason": f"В {edge_name} части несколько слов не удалось уверенно сопоставить с текстом.",
            })
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        # Ignore edges: we may have selected a manuscript window starting or
        # ending in the middle of a spoken word/sentence.
        if i1 < lower_a or i2 > upper_a or j1 < lower_b or j2 > upper_b:
            continue
        old = book_tokens[start+i1:start+i2]
        new = spoken[j1:j2]
        if weak_ending_change(old, new):
            continue
        if tag == "replace" and len(old) == 1 and len(new) == 1:
            if len(normalize(old[0])) < 3 and len(normalize(new[0])) < 3 and old[0] not in IMPORTANT_SHORT:
                continue
        if tag == "delete" and len(old) == 1 and normalize(old[0]) in {"а", "и", "о", "в", "у"}:
            # These are often ASR errors. Important omissions can be reviewed in full log later.
            continue
        if j1 < len(words):
            t_start = words[j1]["start"]
            t_end = words[max(j1, j2-1)]["end"]
        else:
            t_start = words[-1]["end"]
            t_end = t_start
        if tag == "insert":
            repeat = recent_repeat(new, book_tokens[max(0,start+i1-50):start+i1], book_tokens[start+i1:start+i1+50])
            kind = "repeat" if repeat else "extra"
            sev = "high" if repeat or len(new) >= 4 else "review"
            reason = ("Вне текста звучит дополнительное повторное чтение; вероятный дубль."
                      if repeat else "Произнесены слова, которых нет в исходном тексте. Возможно, самопоправка или реплика.")
        elif tag == "delete":
            kind, sev = "omission", "high" if len(old) >= 2 or normalize(old[0]) == "не" else "review"
            reason = "В записи не обнаружен фрагмент исходного текста. Требуется прослушивание."
        else:
            kind = "replacement"
            sev = "high" if len(old) >= 2 or len(new) >= 2 or (old and new and normalize(old[0]) != normalize(new[0])) else "review"
            reason = "Распознанная речь расходится с оригиналом; возможно, оговорка или неверный термин."
        if max(len(old), len(new)) > 45:
            kind, sev = "uncertain", "review"
            reason = "Большой несовпадающий участок. Возможна ошибка синхронизации; прослушайте вручную."
        issues.append({
            "kind":kind,"severity":sev,"confidence":"medium" if sev == "high" else "low",
            "start":round(float(t_start),2), "end":round(float(t_end),2),
            "spoken":" ".join(new[:30]) or "—",
            "expected":" ".join(old[:30]) or "—",
            "reason":reason,
        })
    # Include the preceding part tail so a duplicated sentence crossing a report
    # boundary is still found and assigned to the new part.
    repeats = find_explicit_repeats((prev_tail_words or []) + words, expected)
    repeats = [r for r in repeats if r["start"] >= words[0]["start"]]
    # Remove overlapping duplicate warnings, favour alignment-derived repeats.
    for r in repeats:
        if not any(x["kind"] == "repeat" and abs(x["start"]-r["start"]) < 8 for x in issues):
            issues.append(r)
    for issue in issues:
        digest = f'{issue["kind"]}|{issue["start"]:.2f}|{issue["spoken"]}|{issue["expected"]}'
        issue["id"] = hashlib.sha256(digest.encode("utf-8")).hexdigest()[:14]
    issues.sort(key=lambda x: (x["start"], x["kind"]))
    new_cursor = min(len(book_tokens), max(cursor, start + last.a + last.size))
    return issues, new_cursor, {"matched": quality, "votes": votes, "note": "Кандидаты на исправление, не доказанные ошибки"}
