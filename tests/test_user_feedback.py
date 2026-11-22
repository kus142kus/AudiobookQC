"""Regression tests prompted by real audiobook reviewer feedback."""
from __future__ import annotations
import http.client
import io
import json
import math
import struct
import sys
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from audiobook_qc.analysis import effective_severity, low_value_difference, compare, tokenize
from audiobook_qc.report import generate_report
from audiobook_qc import worker


class FilteringTests(unittest.TestCase):
    def test_numbers_and_hyphenation_not_high(self):
        for a,b in (("500", "Пятьсот"),
                    ("голубовато прозрачном", "голубовато-прозрачном"),
                    ("свершин", "с вершин")):
            with self.subTest(a=a,b=b):
                self.assertTrue(low_value_difference(a,b))
                self.assertEqual(effective_severity({"kind":"replacement", "severity":"high", "spoken":a,"expected":b}), "review")

    def test_one_word_asr_near_misses_stay_in_log(self):
        item = {"kind":"replacement", "severity":"high", "spoken":"Книгиня", "expected":"княгиня"}
        self.assertEqual(effective_severity(item), "review")

    def test_retakes_and_long_omissions_still_important(self):
        self.assertEqual(effective_severity({"kind":"repeat", "severity":"high"}), "high")
        self.assertEqual(effective_severity({"kind":"omission", "severity":"high", "spoken":"—", "expected":"прошёл по старой дороге и вышел"}), "high")

    def test_semantic_negation_preserved(self):
        issue={"kind":"replacement", "severity":"high", "spoken":"они не захотели", "expected":"они захотели"}
        self.assertEqual(effective_severity(issue), "high")
        change={"kind":"replacement", "severity":"high", "spoken":"не надо", "expected":"не дано"}
        self.assertEqual(effective_severity(change), "high")

    def test_legacy_report_reclassified_without_transcription(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/"part.docx"
            old=[{"id":"noise", "kind":"replacement","severity":"high", "start":3,"end":4,
                  "spoken":"500", "expected":"Пятьсот", "reason":"Различие распознавания"},
                 {"id":"repeat","kind":"repeat","severity":"high", "start":8,"end":9,
                  "spoken":"он снова вернулся он снова вернулся", "expected":"одна фраза", "reason":"Повтор"}]
            generate_report(file,{"audio_name":"book.wav", "text_name":"text.docx"},old,0,1200)
            from docx import Document
            doc=Document(file)
            content=" ".join(cell.text for t in doc.tables for row in t.rows for cell in row.cells)
            self.assertNotIn("Пятьсот", content)
            self.assertIn("он снова вернулся", content)


class PlaybackTests(unittest.TestCase):
    def test_native_clip_load_and_seek_without_browser(self):
        from audiobook_qc.desktop import ClipPlayer
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "clip.wav"
            with wave.open(str(path), "wb") as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
                wf.writeframes(b"\0\0" * (16000 * 26))
            player = ClipPlayer()
            player.load(path)
            self.assertGreater(player.length,25)
            player.seek(14.5)
            self.assertAlmostEqual(player.current(),14.5)
            player.stop()

    def test_windows_ffmpeg_launch_is_windowless(self):
        with patch.object(worker.os,"name","nt"):
            self.assertEqual(worker.quiet_subprocess_kwargs()["creationflags"],0x08000000)


if __name__=="__main__":unittest.main()
