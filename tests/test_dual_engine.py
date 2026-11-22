"""Independent checks and regression tests with deterministic decoders.

NOT a claim about real-life WER or speed: true model requires weights/runtime.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from audiobook_qc.dual_engine import (
    classify_independent, verify_with_gigaam, resolve_secondary_dir, MAX_CLIP_SECONDS,
    omission_supported, contains_repeat, extract_transcription_text, read_16k_pcm_wav, chunked_md5)
from audiobook_qc.report import generate_report
from audiobook_qc.analysis import effective_severity
from docx import Document


def candidate(kind='replacement', spoken='в доме находился', expected='в городе находился', start=50):
    return {'kind': kind, 'spoken': spoken, 'expected': expected, 'start': start,
            'end': start+1.5, 'severity': 'high', 'confidence': 'low',
            'reason': 'Первый проход', 'id': 'xyz123'}


class DualModelTests(unittest.TestCase):
    def test_first_model_error_is_suppressed_by_independent_model(self):
        x=classify_independent(candidate(), 'Дальше в городе находился старый дом.')
        self.assertEqual(x['severity'], 'review')
        self.assertFalse(x.get('dual_confirmed', False))

    def test_genuine_candidate_needs_agreement(self):
        x=classify_independent(candidate(), 'Он сказал, что в доме находился мастер.')
        self.assertEqual(x['severity'],'high')
        self.assertTrue(x['dual_confirmed'])
        self.assertIn('GigaAM',x['verifier'])

    def test_unknown_second_asr_never_confirms(self):
        x=classify_independent(candidate(), 'звук не распознан')
        self.assertEqual(x['severity'], 'review')

    def test_omission_without_context_not_promoted(self):
        x=classify_independent(candidate(kind='omission', spoken='—', expected='очень важная фраза'), 'и конец')
        self.assertEqual(x['severity'],'review')

    def test_verifier_never_reads_audio_over_25_seconds(self):
        times=[]
        def fake_cut(audio,start,length,out):
            times.append((start,length))
            out.write_bytes(b'fake')
        issue=candidate()
        with tempfile.TemporaryDirectory() as d:
            result=verify_with_gigaam(object(),Path('book.wav'),[issue],1000,fake_cut,
                decoder=lambda model,path:'у нас в доме находился мастер')
        self.assertLessEqual(times[0][1],MAX_CLIP_SECONDS)
        self.assertTrue(result[0]['dual_confirmed'])

    def test_verification_failure_keeps_candidate_not_high(self):
        with tempfile.TemporaryDirectory():
            result=verify_with_gigaam(object(),Path('file'),[candidate()],1000,
                lambda *args: (_ for _ in ()).throw(RuntimeError('test error')))
        self.assertEqual(result[0]['severity'],'review')
        self.assertNotIn('dual_confirmed',result[0])

    def test_second_model_is_required_offline(self):
        with tempfile.TemporaryDirectory() as root:
            with patch('audiobook_qc.dual_engine.bundled_root', return_value=Path(root)), \
                 patch.dict(os.environ,{'AUDIOBOOK_QC_MODELS_DIR':root}):
                with self.assertRaises(FileNotFoundError):resolve_secondary_dir()

    def test_export_requires_dual_confirmation(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'part.docx'
            base={'audio_name':'book.wav','text_name':'book.txt', 'report_policy':'dual-gigaam'}
            a=candidate(start=5)
            b=candidate(spoken='в доме находился',expected='в городе находился', start=12)
            b.update({'rechecked':True,'dual_confirmed':True})
            generate_report(p,base,[a,b],0,1200)
            text=' '.join(cell.text for table in Document(p).tables for row in table.rows for cell in row.cells)
            self.assertIn('в доме находился',text)
            self.assertIn('Отобрано кандидатов: 1',text)

    def test_long_omission_rechecks_ordered_context(self):
        issue=candidate(kind='omission',spoken='—',expected='это важная пропущенная фраза')
        issue['before_anchor']='в одной старой комнате'
        issue['after_anchor']='он снова заплакал'
        x=classify_independent(issue,'в одной старой комнате он снова заплакал')
        self.assertTrue(x.get('dual_confirmed'))
        self.assertEqual(x['severity'],'high')
        x=classify_independent(issue,'в одной старой комнате это важная пропущенная фраза он снова заплакал')
        self.assertEqual(x['severity'],'review')

    def test_omission_requires_both_context_anchors(self):
        issue=candidate(kind='omission',spoken='—',expected='пропущенная важная фраза')
        issue['before_anchor']='в одной старой комнате'
        issue['after_anchor']='он снова заплакал'
        self.assertFalse(omission_supported(issue,'в одной старой комнате потом стало светло'))

    def test_retake_can_be_confirmed_by_independent_decoder(self):
        issue=candidate(kind='repeat',spoken='он вошел в темную комнату он вошел в темную комнату',expected='он вошел в темную комнату')
        x=classify_independent(issue,'он вошел в темную комнату он вошел в темную комнату')
        self.assertTrue(x['dual_confirmed'])
        self.assertTrue(contains_repeat('он вошел в темную комнату он вошел в темную комнату'))
        x=classify_independent(issue,'он вошел в темную комнату и заснул')
        self.assertEqual(x['severity'],'review')

    def test_one_word_distinct_candidate_is_double_checked(self):
        example=candidate(spoken='доме', expected='городе')
        example['severity']='review'
        example['before_anchor']='он издалека увидел'
        example['after_anchor']='где жил учитель'
        with tempfile.TemporaryDirectory() as d:
            result=verify_with_gigaam(object(), Path('source.mp3'), [example], 1000,
                lambda audio,start,length,out:out.write_bytes(b'fake'),
                decoder=lambda model,path:'он издалека увидел доме где жил учитель')
        self.assertTrue(result[0].get('dual_confirmed'))
        self.assertEqual(effective_severity(result[0]), 'high')


    def test_single_word_without_neighboring_context_is_not_promoted(self):
        example=candidate(spoken='доме',expected='городе')
        out=classify_independent(example,'потом наступила ночь и в доме все уснули')
        self.assertEqual(out['severity'],'review')
        self.assertFalse(out.get('dual_confirmed',False))

    def test_single_word_with_neighboring_context_is_confirmed(self):
        example=candidate(spoken='доме',expected='городе')
        example.update(before_anchor='он издалека увидел',after_anchor='где жил учитель')
        out=classify_independent(example,'он издалека увидел доме где жил учитель')
        self.assertTrue(out.get('dual_confirmed',False))

    def test_gigaam_wav_reader_requires_no_external_ffmpeg(self):
        import wave
        import numpy as np
        with tempfile.TemporaryDirectory() as d:
            wav=Path(d)/'clip.wav'
            with wave.open(str(wav),'wb') as out:
                out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000)
                out.writeframes(np.array([0,16384,-16384,32767],dtype='<i2').tobytes())
            with patch('subprocess.run',side_effect=AssertionError('external subprocess called')):
                result=read_16k_pcm_wav(str(wav))
            self.assertEqual(len(result),4)
            self.assertAlmostEqual(float(result[1]),.5,places=4)
            self.assertAlmostEqual(float(result[2]),-.5,places=4)

    def test_gigaam_wav_reader_rejects_non_pcm_mono(self):
        import wave
        with tempfile.TemporaryDirectory() as d:
            wav=Path(d)/'stereo.wav'
            with wave.open(str(wav),'wb') as out:
                out.setnchannels(2);out.setsampwidth(2);out.setframerate(16000)
                out.writeframes(b'\0'*64)
            with self.assertRaises(ValueError):read_16k_pcm_wav(str(wav))

    def test_chunked_hash_does_not_need_full_checkpoint_in_memory(self):
        import hashlib
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'fake.ckpt'
            f.write_bytes(b'ABCD' * 10000)
            self.assertEqual(chunked_md5(str(f)), hashlib.md5(f.read_bytes()).hexdigest())

    def test_real_gigaam_structured_result_is_accepted(self):
        from types import SimpleNamespace
        response = SimpleNamespace(text='в доме находился', words=None)
        self.assertEqual(extract_transcription_text(response), 'в доме находился')
        with tempfile.TemporaryDirectory():
            out=verify_with_gigaam(object(),Path('source.wav'),[candidate()],500,
                lambda audio,start,length,target:target.write_bytes(b'dummy'),
                decoder=lambda model,clip:response)
        self.assertTrue(out[0].get('dual_confirmed'))

    def test_unreadable_or_silent_secondary_is_not_confirmation(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory():
            for response in (SimpleNamespace(words=[]), SimpleNamespace(text='  ')):
                out=verify_with_gigaam(object(),Path('source.wav'),[candidate()],500,
                    lambda audio,start,length,target:target.write_bytes(b'dummy'),
                    decoder=lambda model,clip:response)
                self.assertEqual(out[0]['severity'],'review')
                self.assertFalse(out[0].get('dual_confirmed',False))

    def test_both_variants_in_one_clip_stay_uncertain(self):
        out=classify_independent(candidate(),
            'В доме находился сторож. Потом в городе находился другой человек.')
        self.assertEqual(out['severity'],'review')
        self.assertFalse(out.get('dual_confirmed',False))

    def test_unrelated_repeat_does_not_confirm_candidate(self):
        issue=candidate(kind='repeat',spoken='он вошел в темную комнату',expected='он вошел в темную комнату')
        out=classify_independent(issue, 'долго шли по улице города долго шли по улице города')
        self.assertFalse(out.get('dual_confirmed',False))
        self.assertEqual(out['severity'],'review')

if __name__ == '__main__':
    unittest.main()

class DualPipelineIntegrationTests(unittest.TestCase):
    def test_worker_invokes_both_models_and_writes_partial_report(self):
        from audiobook_qc import worker
        import uuid
        import wave
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)
            pid=uuid.uuid4().hex
            project=base/'projects'/pid
            (project/'inputs').mkdir(parents=True)
            (project/'inputs'/'text.txt').write_text('В городе находился дом. '*50,encoding='utf-8')
            with wave.open(str(project/'inputs'/'audio.wav'),'wb') as f:
                f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000)
                f.writeframes(b'\0\0'*16000*3)
            job={'id':pid,'audio_name':'audio.wav','text_name':'text.txt',
                 'audio_stored':'audio.wav','text_stored':'text.txt',
                 'duration':3.0,'part_minutes':20,'model':'medium',
                 'report_policy':'dual-gigaam', 'status':'ready',
                 'cursor':0,'processed_seconds':0,
                 'parts':[{'index':1,'start':0,'end':3,'status':'pending'}]}
            class Secondary:
                def transcribe(self,clip):
                    from types import SimpleNamespace
                    return SimpleNamespace(text='в доме находился', words=None)
            issue=candidate(start=1)
            issue.update({'spoken':'в доме находился','expected':'в городе находился'})
            def fake_cut(src,start,length,out):out.write_bytes(b'placeholder')
            with patch.object(worker,'ROOT',base), \
                 patch.object(worker,'load_model',return_value=object()) as first, \
                 patch('audiobook_qc.dual_engine.load_secondary_model',return_value=Secondary()) as second, \
                 patch.object(worker,'cut_audio',side_effect=fake_cut), \
                 patch.object(worker,'transcribe_chunk',return_value=[{'word':'в', 'start':1,'end':1.3}]), \
                 patch.object(worker,'inspect_wav',return_value=[]), \
                 patch.object(worker,'compare',return_value=([issue],5,{'matched':0.9})), \
                 patch.object(worker,'detect_unscripted_profanity',return_value=[]):
                worker.save_job(job)
                worker.model_worker(pid)
                done=worker.load_job(pid)
                self.assertEqual(done['status'],'completed')
                self.assertTrue((project/'reports'/'Часть_001.docx').is_file())
                self.assertTrue((project/'reports'/'Полный_отчёт.docx').is_file())
                self.assertTrue(worker.read_json(project/'parts'/'part-001.json')['issues'][0].get('dual_confirmed'))
                first.assert_called_once()
                second.assert_called_once()

