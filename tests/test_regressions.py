"""Adversarial regression checks for long recordings and honest reporting."""
import json
import math
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
import threading
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from audiobook_qc import worker
from audiobook_qc.acoustics import inspect_wav
from audiobook_qc.analysis import compare, tokenize
from audiobook_qc.report import generate_report


class AcousticChecks(unittest.TestCase):
    def _wav(self, path, data):
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(struct.pack("<" + "h" * len(data), *data))

    def test_real_pcm_clipping_candidate(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "clipping.wav"
            self._wav(p, [32767] * 32000)
            issues = inspect_wav(p, 100., 100., 102.)
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0]["kind"], "clipping")
            self.assertAlmostEqual(issues[0]["start"], 100.)

    def test_ordinary_sine_wave_not_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sine.wav"
            self._wav(p, [int(14000 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(32000)])
            self.assertEqual(inspect_wav(p, 5., 5., 7.), [])


class ReliabilityTests(unittest.TestCase):
    def test_no_speech_is_not_reported_as_success_without_warning(self):
        issues, cursor, alignment = compare(tokenize("Здесь очень много важных слов о прошлом и будущем."), [])
        self.assertEqual(cursor, 0)
        self.assertEqual(issues[0]["kind"], "uncertain")
        self.assertEqual(issues[0]["confidence"], "low")

    def test_word_heading_has_no_banned_phrase(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d)/"report.docx"
            generate_report(f, {"audio_name":"a.mp3", "text_name":"b.docx"}, [], 0, 1200)
            from docx import Document
            doc = Document(f)
            content = " ".join(p.text for p in doc.paragraphs)
            self.assertNotIn("В ПЕРВУЮ ОЧЕРЕДЬ", content)
            self.assertIn("ПОВТОРЫ И ДУБЛИ", content)

    def test_corrupt_cache_recomputed_and_report_generated(self):
        with tempfile.TemporaryDirectory() as d:
            root, pid = Path(d), "b" * 32
            base = root / "projects" / pid
            (base / "inputs").mkdir(parents=True)
            (base / "inputs" / "text.txt").write_text("Вот длинный русский текст для аудиокниги. " * 30, encoding="utf-8")
            (base / "inputs" / "audio.wav").write_bytes(b"fake audio; model is mocked")
            job = {"id": pid, "model": "base", "audio_name": "a.wav", "text_name": "t.txt",
                   "audio_stored": "audio.wav", "text_stored": "text.txt", "duration": 120.,
                   "status": "ready", "processed_seconds": 0, "cursor": 0, "message": "",
                   "parts": [{"index":1, "start":0., "end":120., "status":"pending", "issue_count":0}]}
            with patch.object(worker, "ROOT", root), patch.object(worker,"load_model",return_value=object()), \
                 patch.object(worker,"cut_audio"), patch.object(worker,"inspect_wav",return_value=[]), \
                 patch.object(worker,"transcribe_chunk",return_value=[]) as transcription:
                worker.save_job(job)
                f = base / "chunks" / "p001_c000.json"
                f.parent.mkdir(parents=True)
                f.write_text("{BROKEN")
                worker.model_worker(pid)
                self.assertEqual(worker.load_job(pid)["status"],"completed")
                self.assertEqual(transcription.call_count, 1)
                self.assertTrue((base / "reports" / "Часть_001.docx").is_file())
                self.assertEqual(worker.read_json(base / "parts" / "part-001.json")["issues"][0]["start"],0.0)
                self.assertTrue(worker.read_json(base / "chunks" / "p001_c000.json"))



if __name__ == "__main__":
    unittest.main()

class SpeechCandidateTests(unittest.TestCase):
    def test_unscripted_profanity_requires_audio_review(self):
        from audiobook_qc.analysis import detect_unscripted_profanity
        book = tokenize("Здесь был обычный текст без подобных реплик.")
        words = [{"word":"блядь", "start":56., "end":56.4}]
        result = detect_unscripted_profanity(book, words)
        self.assertEqual(result[0]["kind"], "profanity")
        self.assertEqual(result[0]["severity"], "high")
        self.assertEqual(result[0]["confidence"], "low")
        self.assertEqual(detect_unscripted_profanity(book + ["блядь"], words), [])

    def test_docx_preserves_paragraph_table_paragraph_order(self):
        from docx import Document
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.docx"
            doc = Document()
            doc.add_paragraph("Начало истории")
            table = doc.add_table(rows=1, cols=1)
            table.cell(0, 0).text = "Середина истории"
            doc.add_paragraph("Конец истории")
            doc.save(path)
            text = worker.source_text(path)
            self.assertLess(text.index("Начало"), text.index("Середина"))
            self.assertLess(text.index("Середина"), text.index("Конец"))

class PauseResumeTests(unittest.TestCase):
    def test_pause_after_first_partial_report_then_resume(self):
        with tempfile.TemporaryDirectory() as d:
            root, pid = Path(d), "c" * 32
            base = root / "projects" / pid
            (base / "inputs").mkdir(parents=True)
            (base / "inputs" / "text.txt").write_text("Его история началась в тишине. " * 100, encoding="utf-8")
            (base / "inputs" / "audio.wav").write_bytes(b"synthetic placeholder")
            job = {"id": pid, "model": "base", "audio_name": "book.wav", "text_name": "book.txt",
                   "audio_stored": "audio.wav", "text_stored": "text.txt", "duration": 300.,
                   "status": "ready", "processed_seconds": 0., "cursor": 0, "message": "",
                   "parts": [{"index": 1, "start":0., "end":150., "status":"pending", "issue_count":0},
                             {"index": 2, "start":150., "end":300., "status":"pending", "issue_count":0}]}
            real = worker.generate_report
            def spy(path, job, issues, begin, end, **kw):
                real(path, job, issues, begin, end, **kw)
                if path.name == "Часть_001.docx":
                    worker.STOP_FLAGS[pid].set()
            with patch.object(worker,"ROOT",root), patch.object(worker,"load_model",return_value=object()), \
                 patch.object(worker,"cut_audio"), patch.object(worker,"transcribe_chunk",return_value=[]), \
                 patch.object(worker,"inspect_wav",return_value=[]), \
                 patch.object(worker,"generate_report",side_effect=spy):
                worker.save_job(job)
                worker.STOP_FLAGS[pid] = threading.Event()
                worker.model_worker(pid)
                paused = worker.load_job(pid)
                self.assertEqual(paused["status"],"paused")
                self.assertEqual(paused["parts"][0]["status"],"done")
                self.assertEqual(paused["parts"][1]["status"],"pending")
                self.assertTrue((base / "reports" / "Часть_001.docx").exists())
                self.assertFalse((base / "reports" / "Полный_отчёт.docx").exists())
            with patch.object(worker,"ROOT",root), patch.object(worker,"load_model",return_value=object()), \
                 patch.object(worker,"cut_audio"), patch.object(worker,"transcribe_chunk",return_value=[]), \
                 patch.object(worker,"inspect_wav",return_value=[]):
                worker.STOP_FLAGS[pid] = threading.Event()
                worker.model_worker(pid)
                self.assertEqual(worker.load_job(pid)["status"],"completed")
                self.assertTrue((base / "reports" / "Часть_002.docx").exists())
                self.assertTrue((base / "reports" / "Полный_отчёт.docx").exists())

class ExportAndFFmpegTests(unittest.TestCase):
    def test_ffmpeg_real_wav_extraction_by_time(self):
        with tempfile.TemporaryDirectory() as d:
            src, clip = Path(d)/"source.wav", Path(d)/"clip.wav"
            with wave.open(str(src), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(b"\x00\x00" * 64000)
            self.assertAlmostEqual(worker.audio_duration(src), 4, delta=.2)
            worker.cut_audio(src, 1, 2, clip)
            with wave.open(str(clip)) as w:
                self.assertEqual(w.getframerate(),16000)
                self.assertAlmostEqual(w.getnframes()/16000,2.,delta=.04)

