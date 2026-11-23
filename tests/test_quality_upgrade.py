"""Cross-component and deterministic signal tests for the new QC pipeline."""
import hashlib
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch, MagicMock
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))

from audiobook_qc.acoustics import inspect_wav
from audiobook_qc.events import AudioEventDetector, _manifest_verified
from audiobook_qc.linguistics import glossary_lines, annotate_term_issues, acoustic_stress_candidates
from audiobook_qc.report import generate_report
from audiobook_qc.desktop import delete_saved_project
from audiobook_qc.desktop_web import Bridge, bundled_html
from audiobook_qc import worker


def wav(path, samples, rate=16000):
    with wave.open(str(path),'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(rate)
        w.writeframes(np.clip(np.asarray(samples)*32767,-32768,32767).astype('<i2').tobytes())

class SoundTests(unittest.TestCase):
    def test_click_is_review_only_and_timed(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'s.wav';x=np.zeros(16000*4);x[16000+120]=.99;wav(p,x)
            issues=inspect_wav(p,0,0,4)
            hits=[i for i in issues if i['kind']=='click']
            self.assertTrue(hits)
            self.assertTrue(0.9<hits[0]['start']<1.1)
            self.assertEqual(hits[0]['severity'],'review')

    def test_50hz_hum_flagged_in_silence_but_not_as_clipping(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'s.wav';t=np.arange(16000*7)/16000
            wav(p,np.sin(t*2*np.pi*50)*.02)
            kinds={i['kind'] for i in inspect_wav(p,0,0,7)}
            self.assertIn('hum',kinds)
            self.assertNotIn('clipping',kinds)

    def test_hiss_flagged_deterministically(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'s.wav'
            wav(p,np.random.default_rng(11).normal(0,.009,16000*7))
            self.assertIn('hiss',{i['kind'] for i in inspect_wav(p,0,0,7)})

    def test_quiet_clean_sine_no_false_cough_bell(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'s.wav';t=np.arange(16000*7)/16000
            wav(p,np.sin(t*2*np.pi*440)*.012)
            kinds={i['kind'] for i in inspect_wav(p,0,0,7)}
            self.assertNotIn('hiss',kinds);self.assertNotIn('hum',kinds)
            self.assertNotIn('clipping',kinds)
            self.assertNotIn('sound_event',kinds)

    def test_overlap_ownership(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'s.wav';t=np.arange(16000*8)/16000
            wav(p,np.sin(t*2*np.pi*50)*.02)
            issues=inspect_wav(p,0,4,8)
            self.assertTrue(all(4<=i['start']<8 for i in issues))

class EventTests(unittest.TestCase):
    class Input:
        name='waveform'
    class Model:
        def get_inputs(self):return [EventTests.Input()]
        def run(self,_, feed):
            import numpy as np
            z=np.zeros((2,5),dtype='float32')
            z[0,1]=.96;z[1,1]=.89
            return [z]

    def test_yamnet_predictions_grouped_and_not_called_proven(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'t.wav';wav(p,np.ones(16000*2)*.02)
            detector=AudioEventDetector(session=self.Model(),classes=['Speech','Cough','Knock','Silence','Music'])
            result=detector.inspect(p,0,0,2)
            self.assertEqual(len(result),1)
            self.assertEqual(result[0]['kind'],'sound_event')
            self.assertEqual(result[0]['subtype'],'cough')
            self.assertIn('предположила',result[0]['reason'])
            self.assertTrue(result[0]['end']<=2)

    def test_silence_skips_inference(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'t.wav';wav(p,np.zeros(16000*2))
            model=self.Model();model.run=MagicMock(side_effect=AssertionError('should not run'))
            self.assertEqual(AudioEventDetector(session=model,classes=['Speech','Cough','Knock','Silence','Music']).inspect(p,0,0,2),[])

    def test_corrupt_event_model_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);meta={'files':{}}
            for name in ('yamnet.onnx','yamnet_class_map.csv'):
                f=d/name;f.write_bytes(b'hello')
                meta['files'][name]={'size':5,'sha256':hashlib.sha256(b'hello').hexdigest()}
            (d/'manifest.json').write_text(json.dumps(meta),encoding='utf8')
            _manifest_verified(d)
            (d/'yamnet.onnx').write_bytes(b'broken')
            with self.assertRaises(ValueError):_manifest_verified(d)

class LinguisticTests(unittest.TestCase):
    def test_glossary_marks_stress_and_protects_ordinary_words(self):
        d=glossary_lines('княги́ня\nПотёмкин\nли+са\n# комментарий\n')
        self.assertIn('княгиня',d)
        self.assertEqual(d['княгиня']['accent'],1)
        self.assertIsNone(d['потемкин']['accent'])
        self.assertEqual(d['лиса']['accent'],0)

    def test_user_term_is_annotated_without_forced_priority(self):
        issue={'kind':'replacement','reason':'Расхождение','expected':'княгиня','spoken':'книгиня','severity':'review'}
        annotate_term_issues([issue], 'Упоминалась княгиня.', glossary_lines('княги́ня'))
        self.assertTrue(issue['term_flag'])
        self.assertEqual(issue['severity'],'review')

    def test_acoustic_stress_does_not_claim_proven_wrong(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'s.wav';rate=16000
            x=np.r_[np.sin(2*np.pi*260*np.arange(6000)/rate)*.2,
                     np.sin(2*np.pi*260*np.arange(6000)/rate)*.008,
                     np.zeros(4000)]
            wav(p,x)
            word=[{'word':'лиса','start':0,'end':.75}]
            results=acoustic_stress_candidates(p,0,0,1,word,glossary_lines('лиса́'))
            if results:
                self.assertEqual(results[0]['severity'],'review')
                self.assertIn('ГРУБАЯ',results[0]['reason'])

class IntegrationTests(unittest.TestCase):
    def test_bridge_imports_and_html_contract(self):
        import audiobook_qc.desktop as desk
        self.assertIs(desk.delete_saved_project,delete_saved_project)
        self.assertTrue(callable(Bridge().delete))
        self.assertIn('id="glossary"',bundled_html())

    def test_new_quality_model_does_not_leak_into_legacy(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)
            src=p/'book.txt';src.write_text('Москва. '*50,encoding='utf-8')
            audio=p/'book.wav';wav(audio,np.zeros(16000))
            with patch.object(worker,'ROOT',p/'data'),\
                 patch('audiobook_qc.desktop.resolve_model'),\
                 patch('audiobook_qc.dual_engine.resolve_secondary_dir'),\
                 patch('audiobook_qc.events._manifest_verified'),\
                 patch('audiobook_qc.desktop.worker.audio_duration',return_value=1200):
                from audiobook_qc.desktop import create_project
                pid=create_project(audio,src,glossary_text='княги́ня')
            meta=worker.load_job(pid) if False else json.loads((p/'data'/'projects'/pid/'job.json').read_text(encoding='utf-8'))
            self.assertEqual(meta['quality_mode'],'extended-v1')
            self.assertEqual((p/'data'/'projects'/pid/'inputs'/'glossary.txt').read_text(encoding='utf-8'),'княги́ня')

    def test_word_separates_unverified_sound_from_high_findings(self):
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/'test.docx'
            project={'audio_name':'a.wav','text_name':'b.txt','report_policy':'dual-gigaam'}
            findings=[{'kind':'sound_event','severity':'review','confidence':'low','id':'1',
                       'start':40,'end':41,'spoken':'—','expected':'—','reason':'Возможный кашель'}]
            generate_report(out,project,findings,0,60)
            from docx import Document
            doc=Document(out)
            content='\n'.join(p.text for p in doc.paragraphs)
            self.assertIn('ТОЛЬКО ДЛЯ ПРОСЛУШИВАНИЯ',content)
            self.assertIn('не являются установленным браком',content)

if __name__=='__main__':unittest.main()

class FullQualityWorkerTest(unittest.TestCase):
    def test_extended_pipeline_saves_events_in_incremental_word_without_models(self):
        import uuid
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);pid=uuid.uuid4().hex
            base=root/'projects'/pid
            (base/'inputs').mkdir(parents=True)
            (base/'inputs'/'text.txt').write_text('Старая история началась тихим утром. '*50,encoding='utf-8')
            (base/'inputs'/'glossary.txt').write_text('исто́рия',encoding='utf-8')
            wav(base/'inputs'/'audio.wav',np.zeros(16000*3))
            job={'id':pid,'audio_name':'audio.wav','text_name':'text.txt',
                 'audio_stored':'audio.wav','text_stored':'text.txt','duration':3.0,
                 'part_minutes':20,'status':'ready','model':'medium','report_policy':'dual-gigaam',
                 'quality_mode':'extended-v1','cursor':0,'processed_seconds':0,
                 'parts':[{'index':1,'start':0,'end':3,'status':'pending','issue_count':0}]}
            fake_event={'kind':'sound_event','subtype':'cough','severity':'review','confidence':'low',
                        'start':1.,'end':2.,'id':'cough-test','spoken':'—','expected':'—',
                        'reason':'Возможный кашель'}
            class Detector:
                def inspect(self,*args,**kwargs):return [fake_event]
            def fake_cut(audio,start,secs,out):wav(out,np.zeros(max(1,int(secs*16000))))
            with patch.object(worker,'ROOT',root),\
                 patch.object(worker,'load_model',return_value=object()),\
                 patch('audiobook_qc.dual_engine.load_secondary_model',return_value=object()),\
                 patch('audiobook_qc.dual_engine.verify_with_gigaam',side_effect=lambda a,b,issues,c,d,**kw:issues),\
                 patch('audiobook_qc.events.AudioEventDetector',return_value=Detector()),\
                 patch.object(worker,'cut_audio',side_effect=fake_cut),\
                 patch.object(worker,'transcribe_chunk',return_value=[]),\
                 patch('audiobook_qc.linguistics.acoustic_stress_candidates',return_value=[]),\
                 patch('audiobook_qc.linguistics.intonation_candidates',return_value=[]):
                worker.save_job(job)
                worker.model_worker(pid)
                done=worker.load_job(pid)
                self.assertEqual(done['status'],'completed',done.get('message'))
                data=json.loads((base/'parts'/'part-001.json').read_text(encoding='utf-8'))
                self.assertTrue(any(i['kind']=='sound_event' for i in data['issues']))
                from docx import Document
                report=Document(base/'reports'/'Часть_001.docx')
                self.assertIn('ЗВУК, УДАРЕНИЯ И ИНТОНАЦИЯ','\n'.join(p.text for p in report.paragraphs))
                self.assertTrue((base/'reports'/'Полный_отчёт.docx').is_file())

class ExtendedSixHourSimulation(unittest.TestCase):
    def test_six_hour_quality_mode_still_exports_18_part_reports(self):
        import uuid
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);pid=uuid.uuid4().hex
            base=root/'projects'/pid
            (base/'inputs').mkdir(parents=True)
            (base/'inputs'/'book.txt').write_text('Далеко от города просыпался вечер. '*100,encoding='utf-8')
            (base/'inputs'/'glossary.txt').write_text('го́род',encoding='utf-8')
            (base/'inputs'/'audio.wav').write_bytes(b'mock')
            parts=[{'index':i+1,'start':i*1200,'end':(i+1)*1200,'status':'pending'} for i in range(18)]
            job={'id':pid,'audio_name':'audio.wav','text_name':'book.txt',
                 'audio_stored':'audio.wav','text_stored':'book.txt','model':'medium',
                 'duration':21600.,'part_minutes':20,'status':'ready',
                 'report_policy':'dual-gigaam','quality_mode':'extended-v1',
                 'cursor':0,'processed_seconds':0,'parts':parts}
            class Detector:
                def inspect(self,*a,**kw):return []
            def fake_cut(*a,**kw):pass
            first_report_available=[]
            original=worker.generate_report
            def spy_report(path,*a,**kw):
                if Path(path).name=='Часть_001.docx':
                    first_report_available.append(not (base/'reports'/'Полный_отчёт.docx').exists())
                return original(path,*a,**kw)
            with patch.object(worker,'ROOT',root),\
                 patch.object(worker,'load_model',return_value=object()),\
                 patch('audiobook_qc.dual_engine.load_secondary_model',return_value=object()),\
                 patch('audiobook_qc.dual_engine.verify_with_gigaam',side_effect=lambda model,a,issues,*args,**kw:issues),\
                 patch('audiobook_qc.events.AudioEventDetector',return_value=Detector()),\
                 patch.object(worker,'cut_audio',side_effect=fake_cut),\
                 patch.object(worker,'transcribe_chunk',return_value=[]),\
                 patch.object(worker,'inspect_wav',return_value=[]),\
                 patch('audiobook_qc.linguistics.acoustic_stress_candidates',return_value=[]),\
                 patch('audiobook_qc.linguistics.intonation_candidates',return_value=[]),\
                 patch.object(worker,'generate_report',side_effect=spy_report):
                worker.save_job(job)
                worker.model_worker(pid)
                finished=worker.load_job(pid)
            self.assertEqual(finished['status'],'completed',finished.get('message'))
            self.assertTrue(all(first_report_available))
            self.assertEqual(len(list((base/'reports').glob('Часть_*.docx'))),18)
            self.assertTrue((base/'reports'/'Полный_отчёт.docx').is_file())
