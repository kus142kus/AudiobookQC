"""Russian proper-name, pronunciation and intonation REVIEW aids.

No orthographic ASR identifies lexical stress. Acoustic stress is intrinsically
ambiguous: candidates from this module are *never* high-certainty errors.
"""
from __future__ import annotations
import hashlib
import re
import unicodedata

VOWELS = 'аеёиоуыэюя'
TOKEN = re.compile(r'[а-яёА-ЯЁ]+(?:[\u0301][а-яёА-ЯЁ]+)*')

def clean(word):
    return unicodedata.normalize('NFC',word).casefold().replace('\u0301','').replace('+','').replace('ё','е')


def glossary_lines(raw: str) -> dict:
    """One entry per line, optional U+0301 or + to mark the stressed vowel.

    Example: `княги́ня` or `княги+ня`. Expressions without stress marks are
    still protected terms/names but cannot be checked for acoustic stress.
    """
    entries={}
    for line in raw.splitlines()[:2000]:
        line=line.strip().split('#',1)[0].strip()
        if not line or len(line)>120: continue
        norm=clean(line)
        if not re.fullmatch(r'[а-яё]+(?:[- ][а-яё]+)*',norm): continue
        vowels=[m.start() for m in re.finditer('[аеёиоуыэюя]',line.lower())]
        stress=None
        if '\u0301' in line:
            idx=line.index('\u0301')
            stressed=[i for i,v in enumerate(vowels) if v==idx-1]
            if stressed:stress=stressed[0]
        elif '+' in line:
            idx=line.index('+')
            stressed=[i for i,v in enumerate(vowels) if v==idx-1]
            if stressed:stress=stressed[0]
        entries[norm]={'display':line,'accent':stress}
    return entries


def auto_names(source: str) -> set[str]:
    """Likely mid-sentence proper names, not a definitive NER classifier."""
    # Filter sentence openings to avoid reporting ordinary capitalized words.
    matches=re.finditer(r'[А-ЯЁ][а-яё]{3,}',source)
    names=set()
    for m in matches:
        prefix=source[max(0,m.start()-12):m.start()]
        if not prefix.strip() or re.search(r'[.!?…]\s*$',prefix):
            continue
        names.add(clean(m.group()))
    return names


def annotate_term_issues(issues: list[dict], source: str, glossary: dict) -> None:
    names=auto_names(source)
    for item in issues:
        if item.get('kind') not in ('replacement','omission','extra'):continue
        tokens=[clean(t) for t in re.findall(r'[а-яёА-ЯЁ-]+',item.get('expected',''))]
        special=next((t for t in tokens if t in glossary or t in names),None)
        if not special: continue
        item['term_flag']=True
        item['term_word']=special
        item['reason']='Возможное искажение имени или специального термина «'+special+'». '+item.get('reason','')
        # Never promote a finding based only on capitalization/glossary.


def acoustic_stress_candidates(wav_path, absolute_start: float, inner_start: float,
                               inner_end: float, words: list[dict], glossary: dict,
                               limit: int = 12) -> list[dict]:
    """Heuristic check only for manually accented words, with strict abstention.

    Samples are divided into roughly syllabic regions of the ASR word interval.
    Forced phoneme alignment is absent; a suspected stress mismatch is a prompt
    to listen, not evidence. This makes the limitation visible to reviewers.
    """
    import wave
    import numpy as np
    targets={k:v for k,v in glossary.items() if v['accent'] is not None and ' ' not in k}
    if not targets:return []
    result=[]
    with wave.open(str(wav_path),'rb') as w:
        rate=w.getframerate()
        if rate!=16000 or w.getnchannels()!=1 or w.getsampwidth()!=2:return []
        raw=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2')
    for word in words:
        spelling=clean(word.get('word',''))
        item=targets.get(spelling)
        if not item: continue
        n=sum(ch in VOWELS for ch in spelling)
        if n<2 or n>6 or item['accent']>=n:continue
        t0,t1=float(word['start']),float(word['end'])
        if t0<inner_start or t0>=inner_end or not .25<=t1-t0<=1.8:continue
        a=max(0,int((t0-absolute_start)*rate));b=min(len(raw),int((t1-absolute_start)*rate))
        if b-a<rate*.20:continue
        # Acoustic energy at rough syllable center, NOT true vowel alignment.
        fragment=raw[a:b].astype(np.float32)/32768.0
        border=np.linspace(0,len(fragment),n+1,dtype=int)
        energies=[float(np.sqrt(np.mean(fragment[border[i]:border[i+1]]**2)))
                  if border[i+1]>border[i] else 0 for i in range(n)]
        observed=int(np.argmax(energies))
        reference=energies[item['accent']]
        if (observed!=item['accent'] and energies[observed]>.045 and
                energies[observed]>reference*2.5 and reference>.003):
            row={'kind':'stress','severity':'review','confidence':'low',
                 'start':round(t0,2),'end':round(t1,2),'spoken':word['word'],
                 'expected':item['display'],
                 'reason':'Предполагаемое отличие ритмического акцента от помеченного в словаре. Это ГРУБАЯ оценка энергии, не распознавание ударения; обязательно проверить на слух.'}
            row['id']=hashlib.sha256(f'stress|{t0:.2f}|{spelling}'.encode()).hexdigest()[:14]
            result.append(row)
            if len(result)>=limit:break
    return result


def intonation_candidates(wav_path, absolute_start: float, inner_start: float,
                          inner_end: float, words: list[dict], manuscript: str,
                          limit: int = 6) -> list[dict]:
    """Flag only unusually strong falls at manuscript questions.

    Russian yes/no questions may legitimately have falling contours. Thus the
    output is experimental review-only, never an 'incorrect intonation' claim.
    """
    import wave
    import numpy as np
    # Find multi-word question endings and their ASR-aligned tail.
    questions=[]
    for sentence in re.findall(r'[^.!?]{8,}\?',manuscript):
        toks=re.findall(r'[а-яёА-ЯЁ]+',sentence)
        if len(toks)>=3:questions.append(tuple(clean(x) for x in toks[-3:]))
    if not questions:return []
    tokens=[clean(w['word']) for w in words]
    with wave.open(str(wav_path),'rb') as w:
        if w.getnchannels()!=1 or w.getsampwidth()!=2 or w.getframerate()!=16000:return []
        data=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').astype(np.float32)/32768
    findings=[]
    for i in range(2,len(words)):
        if tuple(tokens[i-2:i+1]) not in questions:continue
        end=float(words[i]['end'])
        if end<inner_start or end>=inner_end:continue
        a=max(0,int((end-.85-absolute_start)*16000));b=min(len(data),int((end-absolute_start)*16000))
        if b-a<9600:continue
        signal=data[a:b]
        pitches=[]
        for j in range(0,len(signal)-960,640):
            y=signal[j:j+960]
            if float(np.sqrt(np.mean(y*y))) < .015:continue
            y=y-np.mean(y)
            corr=np.correlate(y,y,mode='full')[len(y)-1:]
            lo,hi=40,200  # F0 80..400 Hz at 16kHz
            if hi>=len(corr):continue
            lag=lo+int(np.argmax(corr[lo:hi]))
            if float(corr[lag])/(float(corr[0])+1e-6) > .65:
                pitches.append((j,float(16000/lag)))
        if len(pitches)<7:continue
        first=np.median([p for _,p in pitches[:max(2,len(pitches)//3)]])
        last=np.median([p for _,p in pitches[-max(2,len(pitches)//3):]])
        if first>100 and last < first*.68:
            start=max(inner_start,end-.85)
            row={'kind':'intonation','severity':'review','confidence':'low',
                 'start':round(start,2),'end':round(end,2),
                 'spoken':' '.join(w['word'] for w in words[max(0,i-4):i+1]),
                 'expected':'Вопросительное предложение в рукописи',
                 'reason':'В конце вопроса обнаружено заметное снижение основного тона. В русском языке это может быть нормой; это только подсказка для прослушивания, а не ошибка интонации.'}
            row['id']=hashlib.sha256(f'intonation|{start:.2f}'.encode()).hexdigest()[:14]
            findings.append(row)
            if len(findings)>=limit:break
    return findings
