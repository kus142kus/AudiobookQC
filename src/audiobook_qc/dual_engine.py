"""Independent Russian ASR verifier: GigaAM v3 E2E CTC, strictly offline.

A mismatch in Whisper alone is not evidence of a recording error.  GigaAM has
its own acoustic architecture, and is used on *short excerpts* solely to
assess those mismatch candidates. The two models still may share errors.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Callable

from .analysis import effective_severity, low_value_difference, tokenize, normalize
from .verification import phrase_in, prioritize_rechecks
from .offline import bundled_root, offline_environment

GIGAAM_VERSION = 'v3_e2e_ctc'
GIGAAM_DIRNAME = 'gigaam'
GIGAAM_MODEL = 'v3_e2e_ctc.ckpt'
GIGAAM_TOKENIZER = 'v3_e2e_ctc_tokenizer.model'
HF_FILES = ('config.json', 'modeling_gigaam.py', 'pytorch_model.bin', 'tokenizer.model')
HF_MANIFEST = 'model_manifest.json'
HF_REVISION = 'cec030b4c4f35d928e4a9044a3bdb29ebd499fac'
# Model checksum published by the upstream project's load_model implementation.
GIGAAM_MD5 = '367074d6498f426d960b25f49531cf68'
MAX_DUAL_RECHECKS_PER_PART = 18
MAX_CLIP_SECONDS = 23.0  # Official transcribe() accepts up to 25 seconds.


def extract_transcription_text(transcription) -> str:
    """Normalize GigaAM's real TranscriptionResult(text=..., words=...) API.

    GigaAM 0.2.x returns a structured result, not a plain str. Never promote a
    finding when the secondary model returned no readable transcription.
    """
    if isinstance(transcription, str):  # test doubles / compatible wrappers
        value = transcription
    else:
        value = getattr(transcription, 'text', None)
    if not isinstance(value, str):
        raise TypeError('GigaAM должна вернуть TranscriptionResult с текстовым полем text')
    return value.strip()


def _repeated_phrase_supported(issue: dict, transcript: str) -> bool:
    """A repeat elsewhere in a 23-second crop is NOT evidence for this issue."""
    words = [normalize(w) for w in tokenize(transcript)]
    first_pass = [normalize(w) for w in tokenize(issue.get('spoken', ''))]
    # Require a substantive repeated four-word anchor present in the candidate
    # itself. Short candidates cannot be corroborated this way.
    if len(first_pass) < 4:
        return False
    for n in (7, 6, 5, 4):
        if len(first_pass) < n:
            continue
        possible = {tuple(first_pass[k:k+n]) for k in range(len(first_pass)-n+1)}
        for i in range(len(words)-2*n+1):
            for j in range(i+n, min(len(words)-n+1, i+n+22)):
                phrase = tuple(words[i:i+n])
                if phrase in possible and phrase == tuple(words[j:j+n]):
                    return True
    return False


def resolve_secondary_dir() -> Path:
    from .worker import ROOT
    roots: list[Path] = []
    if os.environ.get('AUDIOBOOK_QC_MODELS_DIR'):
        roots.append(Path(os.environ['AUDIOBOOK_QC_MODELS_DIR']).expanduser())
    roots += [bundled_root() / 'models', ROOT / 'models']
    for base in roots:
        d = base / GIGAAM_DIRNAME
        if (all((d / item).is_file() and (d / item).stat().st_size > 0
                for item in HF_FILES + (HF_MANIFEST,))
                or all((d / item).is_file() and (d / item).stat().st_size > 0
                for item in (GIGAAM_MODEL, GIGAAM_TOKENIZER))):
            return d
    raise FileNotFoundError(
        'Не найдена вторая локальная модель GigaAM v3. Для двухмодельной проверки '
        'папка models/gigaam должна содержать офлайн-файлы второй модели. '
        'Никакие загрузки при анализе не выполняются.'
    )


def check_model_hash(folder: Path) -> None:
    """Fail closed on corrupt weights before invoking an external decoder."""
    if (folder / HF_MANIFEST).is_file():
        manifest = json.loads((folder / HF_MANIFEST).read_text(encoding='utf-8'))
        if manifest.get('revision') != HF_REVISION:
            raise ValueError('Неизвестная ревизия офлайн-модели GigaAM')
        for name in HF_FILES:
            meta = manifest.get('files', {}).get(name, {})
            f = folder/name
            if (not f.is_file() or f.stat().st_size != meta.get('size')
                    or not isinstance(meta.get('sha256'), str)):
                raise ValueError(f'Некорректный файл GigaAM: {name}')
            digest = hashlib.sha256()
            with f.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
            if digest.hexdigest() != meta['sha256']:
                raise ValueError(f'Нарушена целостность модели GigaAM: {name}')
        return
    ckpt = folder / GIGAAM_MODEL
    md5 = hashlib.md5()  # nosec: legacy published checksum only
    with ckpt.open('rb') as fh:
        for data in iter(lambda: fh.read(1024 * 1024), b''):
            md5.update(data)
    if md5.hexdigest() != GIGAAM_MD5:
        raise ValueError('Контрольная сумма модели GigaAM не совпадает с опубликованной.')


def read_16k_pcm_wav(path: str, sample_rate: int = 16000):
    """Read our own FFmpeg-normalized WAV without spawning GigaAM's `ffmpeg`.

    Upstream GigaAM calls an executable literally named `ffmpeg`. Our desktop
    bundle includes imageio-ffmpeg under a platform-specific binary filename;
    on a clean Mac/Windows install `ffmpeg` may not be on PATH. Every verifier
    crop comes from worker.cut_audio and is already 16kHz/mono/PCM16, so use
    Python's wave module instead. This also prevents popping console windows.
    """
    import wave
    import numpy as np
    import torch
    if sample_rate != 16000:
        raise ValueError('GigaAM verifier requires 16 kHz audio')
    with wave.open(str(path), 'rb') as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16000:
            raise ValueError('Ожидается локальный PCM WAV 16 kHz, mono, 16 bit')
        buffer = wav.readframes(wav.getnframes())
    # Copy to writable RAM before mapping into Torch; no downloads or external
    # binaries involved. The 23-second bound keeps memory use predictable.
    pcm = np.frombuffer(buffer, dtype='<i2').copy()
    return torch.from_numpy(pcm).float() / 32768.0


def _install_wav_reader() -> None:
    import gigaam.model as upstream_model
    upstream_model.load_audio = read_16k_pcm_wav


def chunked_md5(path: str) -> str:
    """Replace upstream's eager read-all MD5 for multi-hundred-MB checkpoints."""
    digest = hashlib.md5()  # nosec - matching official upstream checksum
    with open(path, 'rb') as fh:
        for piece in iter(lambda: fh.read(1024 * 1024), b''):
            digest.update(piece)
    return digest.hexdigest()


def load_secondary_model(*, validate_hash: bool = True):
    """Open already-bundled checkpoint without network. CPU for portability."""
    folder = resolve_secondary_dir()
    if validate_hash:
        check_model_hash(folder)
    offline_environment()
    if (folder/HF_MANIFEST).is_file():
        # Publisher-hosted version of the *same* GigaAM v3 E2E CTC architecture.
        # Load solely from the packaged directory, with no CDN fallback.
        from transformers import AutoModel
        model = AutoModel.from_pretrained(
            str(folder), trust_remote_code=True, local_files_only=True)
        model = model.cpu().float().eval()
        # The official HF transcriber otherwise invokes an external `ffmpeg`.
        # Our verifier already creates mono/16-kHz PCM WAV, so read it directly.
        import types
        import torch
        def prepare_pcm(self, wav_file):
            wav = read_16k_pcm_wav(str(wav_file)).to(self._device).to(self._dtype)
            return wav.unsqueeze(0), torch.full(
                (1,), wav.shape[-1], dtype=torch.long, device=self._device)
        model.model.prepare_wav = types.MethodType(prepare_pcm, model.model)
        return model

    # Legacy compatibility for earlier distributions with the official .ckpt.
    import gigaam
    gigaam.hash_path = chunked_md5
    _install_wav_reader()
    return gigaam.load_model(GIGAAM_VERSION, download_root=str(folder),
                             fp16_encoder=False, device='cpu', use_flash=False)


def _reading_supported(issue: dict, phrase: str, transcript: str) -> bool:
    """For isolated words, demand neighboring manuscript context in the crop.

    A common word can occur elsewhere in a 23-second GigaAM response. Without
    context, a match to that word alone is not useful independent evidence.
    Multiword exact phrases are distinctive enough for provisional review.
    """
    words = [normalize(w) for w in tokenize(transcript)]
    needle = [normalize(w) for w in tokenize(phrase)]
    if not needle or not words:
        return False
    if len(needle) >= 2:
        return phrase_in(phrase, transcript)
    before = [normalize(w) for w in tokenize(issue.get('before_anchor', ''))][-2:]
    after = [normalize(w) for w in tokenize(issue.get('after_anchor', ''))][:2]
    if len(before) < 2 and len(after) < 2:
        return False
    for i, token in enumerate(words):
        if token != needle[0]:
            continue
        preceding = len(before) == 2 and any(
            words[j:j+2] == before and 0 <= i-j-2 <= 4
            for j in range(max(0,i-6),i-1))
        following = len(after) == 2 and any(
            words[j:j+2] == after and 0 <= j-i-1 <= 4
            for j in range(i+1,min(len(words)-1,i+6)))
        if preceding or following:
            return True
    return False


def classify_independent(issue: dict, second_text: str) -> dict:
    """Promote only independent corroboration; never call a candidate a proven mistake."""
    out = dict(issue)
    if out.get('kind') not in ('replacement', 'omission', 'extra', 'repeat'):
        return out
    out.update({'rechecked': True, 'dual_checked': True,
                'verifier': 'GigaAM v3 E2E CTC', 'confidence': 'low'})
    expected, spoken = issue.get('expected', ''), issue.get('spoken', '')
    if issue['kind'] == 'repeat':
        if _repeated_phrase_supported(issue, second_text):
            out.update({'severity': 'high', 'confidence':'medium', 'dual_confirmed':True,
                        'reason':'Две разные модели обнаружили последовательное повторное чтение. '
                                 'Проверьте, предусмотрен ли дубль рукописью.'})
        else:
            out.update({'severity':'review',
                        'reason':'GigaAM не обнаружила устойчивого повтора в коротком фрагменте; '
                                 'предупреждение оставлено в полном журнале.'})
        return out
    heard_expected = bool(expected.strip()) and _reading_supported(issue, expected, second_text)
    heard_spoken = bool(spoken.strip()) and _reading_supported(issue, spoken, second_text)
    if heard_expected and heard_spoken:
        out['severity'] = 'review'
        out['reason'] = ('Обе формулировки обнаружены в одном фрагменте. Возможно, это самопоправка, '
                         'либо неточное выравнивание; нужна ручная проверка.')
    elif heard_expected:
        out['severity'] = 'review'
        out['reason'] = ('GigaAM распознала фразу из рукописи. Различие с Whisper '
                         'не подтверждено — вероятна ошибка первого распознавания.')
    elif issue['kind'] == 'omission' and length_of_expected(expected) >= 3 and omission_supported(issue, second_text):
        out.update({'severity':'high','confidence':'medium','dual_confirmed':True,
                    'reason':'Вторая модель распознала текст перед пропуском и после него, '
                             'но не обнаружила промежуточную фразу. Обязательно послушайте участок.'})
    elif heard_spoken and issue['kind'] in ('replacement', 'extra') and (effective_severity(issue) == 'high' or
            (issue['kind'] == 'replacement' and len(tokenize(spoken)) == len(tokenize(expected)) == 1
             and not low_value_difference(spoken, expected))):
        out['severity'] = 'high'
        out['confidence'] = 'medium'
        out['dual_confirmed'] = True
        out['reason'] = ('Whisper и независимая модель GigaAM распознали одинаковое '
                         'отличие от текста. Проверьте аудио: обе модели могут ошибаться.')
    else:
        out['severity'] = 'review'
        out['reason'] = ('GigaAM не подтвердила расхождение достаточно уверенно; '
                         'оставлено в полном журнале для выборочной проверки.')
    return out


def length_of_expected(value: str) -> int:
    return len(tokenize(value))


def omission_supported(issue: dict, transcript: str) -> bool:
    """Avoid labelling omissions solely from absence in an ASR hypothesis."""
    before = [normalize(w) for w in tokenize(issue.get('before_anchor', ''))]
    after = [normalize(w) for w in tokenize(issue.get('after_anchor', ''))]
    words = [normalize(w) for w in tokenize(transcript)]
    if len(before) < 3 or len(after) < 3:
        return False
    # Exact 3-word anchors in the correct relative order; not sufficient proof.
    before, after = before[-3:], after[:3]
    before_starts = [i for i in range(len(words)-2) if words[i:i+3] == before]
    after_starts = [i for i in range(len(words)-2) if words[i:i+3] == after]
    return any(0 <= j-i <= 25 and j >= i+3 for i in before_starts for j in after_starts)


def contains_repeat(transcript: str) -> bool:
    """Find 4+ words repeated shortly afterwards in independent transcription."""
    words = [normalize(w) for w in tokenize(transcript)]
    for size in (6, 5, 4):
        for i in range(len(words)-size*2+1):
            for j in range(i+size, min(len(words)-size+1, i+size+22)):
                if words[i:i+size] == words[j:j+size]:
                    return True
    return False


def verify_with_gigaam(second_model, audio: Path, issues: list[dict], duration: float,
                        cut_audio: Callable, *, decoder=None, should_stop=None,
                        budget: int = MAX_DUAL_RECHECKS_PER_PART) -> list[dict]:
    """Independent, bounded recheck. Does not re-transcribe whole audiobook.

    decoder injected for tests; production calls GigaAM.transcribe(short_wav).
    Unknown / failed checks are never promoted to the Word report.
    """
    result = [dict(issue) for issue in issues]
    repeat_indices = [i for i, issue in enumerate(result)
                      if issue.get('kind') == 'repeat' and effective_severity(issue) == 'high']
    to_verify = repeat_indices[:max(0, budget // 3)]
    to_verify.extend(i for i in prioritize_rechecks(result, max(0, budget))
                     if i not in to_verify and len(to_verify) < budget)
    # Even single-word substitutions can alter the book's meaning. The old
    # high-priority filter hid them all; include distinct one-word candidates
    # for independent confirmation, without promoting similar spellings.
    if len(to_verify) < budget:
        for i, issue in enumerate(result):
            if i in to_verify or issue.get('kind') != 'replacement':
                continue
            spoken, expected = issue.get('spoken', ''), issue.get('expected', '')
            if (len(tokenize(spoken)) == len(tokenize(expected)) == 1
                    and not low_value_difference(spoken, expected)):
                to_verify.append(i)
            if len(to_verify) >= budget:
                break
    to_verify.sort(key=lambda i: float(result[i].get('start', 0)))
    for i in to_verify:
        if should_stop and should_stop():
            break
        issue = result[i]
        midpoint = (float(issue['start']) + float(issue['end'])) / 2
        start = max(0.0, midpoint - 10.0)
        end = min(float(duration), start + MAX_CLIP_SECONDS)
        if end <= start + 0.25:
            continue
        try:
            with tempfile.TemporaryDirectory(prefix='aqc-dual-') as tmp:
                clip = Path(tmp) / 'independent.wav'
                cut_audio(audio, start, end - start, clip)
                transcription = (decoder(second_model, clip) if decoder
                                 else second_model.transcribe(str(clip)))
            secondary_text = extract_transcription_text(transcription)
            if not secondary_text:
                raise ValueError('Вторая модель не распознала речь в проверяемом фрагменте')
            result[i] = classify_independent(issue, secondary_text)
        except Exception as exc:
            # Do not erase evidence or fake independent agreement.
            issue.update({'severity': 'review', 'confidence': 'low',
                          'rechecked': False, 'dual_checked': False,
                          'reason': f'Независимая проверка не выполнена ({type(exc).__name__}). '
                                    'Предупреждение сохранено в полном журнале.'})
    return result
