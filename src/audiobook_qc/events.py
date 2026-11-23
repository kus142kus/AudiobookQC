"""Offline AudioSet/YAMNet event candidates (coughs, knocks, bells, etc).

YAMNet scores are *probabilities-like classification scores*, not proof. All
findings require playback, and no sound class is silently assumed from ASR.
A bundled ONNX file is mandatory in release builds; never fetch at runtime.
"""
from __future__ import annotations
import csv
import hashlib
import os
import sys
import wave
from pathlib import Path

EVENT_LABELS = {
    'cough': ('cough','кашель'),
    'sneeze': ('sneeze','чихание'),
    'knock': ('knock','стук'),
    'tap': ('tap','стук / касание'),
    'slam': ('slam','хлопок'),
    'doorbell': ('doorbell','дверной звонок'),
    'telephone bell': ('telephone bell','телефонный звонок'),
    'ringtone': ('ringtone','мелодия звонка'),
    'alarm': ('alarm','сигнал тревоги'),
    'keyboard': ('keyboard','звуки клавиатуры'),
    'typing': ('typing','набор текста'),
    'dog': ('dog','собака'),
    'music': ('music','музыка / фон'),
    'static': ('static','статический шум'),
    'white noise': ('white noise','белый шум'),
}

def model_dir() -> Path:
    if os.environ.get('AUDIOBOOK_QC_MODELS_DIR'):
        return Path(os.environ['AUDIOBOOK_QC_MODELS_DIR']).expanduser() / 'events'
    base = Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[2]))
    return base / 'models' / 'events'


def _manifest_verified(folder: Path) -> None:
    import json
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    for name in ('yamnet.onnx','yamnet_class_map.csv'):
        expected = manifest['files'][name]
        digest = hashlib.sha256()
        file = folder / name
        with file.open('rb') as stream:
            for b in iter(lambda: stream.read(1024*1024),b''):
                digest.update(b)
        if digest.hexdigest() != expected['sha256'] or file.stat().st_size != expected['size']:
            raise ValueError('Повреждены данные звуковой модели: '+name)


class AudioEventDetector:
    def __init__(self, folder: Path | None = None, *, session=None, classes=None):
        self.folder = Path(folder) if folder else model_dir()
        if session is None:
            _manifest_verified(self.folder)
            import onnxruntime as ort
            session = ort.InferenceSession(str(self.folder/'yamnet.onnx'),
                     providers=['CPUExecutionProvider'], sess_options=self._settings(ort))
        self.session = session
        if classes is None:
            with (self.folder/'yamnet_class_map.csv').open(newline='',encoding='utf-8') as file:
                classes = []
                for row in csv.DictReader(file):
                    classes.append(row.get('display_name') or row.get('class_name') or row.get('name') or '')
        self.classes = [str(c) for c in classes]
        self.wave_input = session.get_inputs()[0].name

    @staticmethod
    def _settings(ort):
        opts=ort.SessionOptions()
        opts.intra_op_num_threads=2
        opts.inter_op_num_threads=1
        return opts

    def inspect(self, path: Path, absolute_start: float, inner_start: float, inner_end: float) -> list[dict]:
        import numpy as np
        with wave.open(str(path),'rb') as wav:
            if wav.getnchannels()!=1 or wav.getframerate()!=16000 or wav.getsampwidth()!=2:
                raise ValueError('События: требуется WAV mono PCM16 16k')
            sr=wav.getframerate()
            start=max(0,int((inner_start-absolute_start)*sr))
            end=min(wav.getnframes(),int((inner_end-absolute_start)*sr))
            wav.setpos(start)
            sound=np.frombuffer(wav.readframes(max(0,end-start)),dtype='<i2').astype(np.float32)/32768
        out=[]
        # At most 8s of audio per inference; fixed RAM for any input length.
        window=sr*8
        for left in range(0,len(sound),window):
            clip=sound[left:left+window]
            if clip.size < 2000 or float(np.sqrt(np.mean(clip**2))) < .00035:
                continue
            scores=np.asarray(self.session.run(None,{self.wave_input:clip.astype(np.float32)})[0])
            if scores.ndim != 2 or scores.shape[1] != len(self.classes):
                raise ValueError('Неверная размерность оценок AudioSet')
            for frame, values in enumerate(scores):
                best=[]
                for idx, score in enumerate(values):
                    if float(score) < .42:
                        continue
                    lab=self.classes[idx].lower()
                    match=next(((k,ru) for k,(en,ru) in EVENT_LABELS.items() if en in lab),None)
                    if match:
                        best.append((float(score),match[0],match[1]))
                if not best: continue
                score,key,ru=max(best)
                # Sound labels are not hard proofs. Speech and narration can be
                # mislabeled, especially as music, noises and taps.
                t=inner_start+left/sr+frame*.48
                if t >= inner_end: continue
                t_end=min(inner_end,t+.96)
                out.append({'kind':'sound_event','subtype':key,'severity':'review',
                            'confidence':'medium' if score>.75 else 'low',
                            'start':round(t,2),'end':round(t_end,2),
                            'spoken':'—','expected':'—','score':round(score,3),
                            'reason':f'Акустическая модель предположила событие «{ru}» (оценка {score:.2f}). Проверьте звук; возможны ложные срабатывания.'})
        # Coalesce repeated overlapping frame predictions for one real event.
        merged=[]
        for item in sorted(out,key=lambda v:(v['start'],v['subtype'])):
            if merged and merged[-1]['subtype']==item['subtype'] and item['start']-merged[-1]['end']<1.2:
                merged[-1]['end']=max(item['end'],merged[-1]['end'])
                merged[-1]['score']=max(item['score'],merged[-1]['score'])
            else:
                merged.append(item)
        for item in merged:
            item['id']=hashlib.sha256(f"event|{item['subtype']}|{item['start']:.2f}".encode()).hexdigest()[:14]
        return sorted(merged,key=lambda d:d['score'],reverse=True)[:18]
