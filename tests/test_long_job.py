"""Simulation of a 6-hour, 18-part job using an ASR test double.

Does not assert acoustic accuracy or real-time speed on actual six-hour audio.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from audiobook_qc import worker


class LongJobTests(unittest.TestCase):
    def test_six_hours_yields_eighteen_reports_incrementally(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            pid="a"*32
            basedir=root/"projects"/pid
            (basedir/"inputs").mkdir(parents=True)
            (basedir/"inputs"/"text.txt").write_text(
                "Глава первая. Так начиналась история о людях и времени. "*100,encoding="utf-8")
            (basedir/"inputs"/"audio.wav").write_bytes(b"dummy mocked audio")
            parts=[{"index":i+1,"start":i*1200,"end":(i+1)*1200,"status":"pending","issue_count":0}
                   for i in range(18)]
            job={"id":pid,"model":"small","audio_name":"6часов.wav","text_name":"text.txt",
                 "audio_stored":"audio.wav","text_stored":"text.txt","duration":21600,
                 "status":"ready","processed_seconds":0,"cursor":0,"message":"", "parts":parts}
            first_report_before_complete=[]
            original=worker.generate_report
            def spy_report(path, data, issues, a, b, **kwargs):
                if path.name=="Часть_001.docx":
                    first_report_before_complete.append(not (basedir/"reports"/"Полный_отчёт.docx").exists())
                return original(path,data,issues,a,b,**kwargs)
            with patch.object(worker,"ROOT",root), patch.object(worker,"load_model",return_value=object()), \
                 patch.object(worker,"cut_audio",return_value=None), \
                 patch.object(worker,"inspect_wav",return_value=[]), \
                 patch.object(worker,"transcribe_chunk",return_value=[]), \
                 patch.object(worker,"generate_report",side_effect=spy_report):
                worker.save_job(job)
                worker.model_worker(pid)
                state=worker.load_job(pid)
                self.assertEqual(state["status"],"completed",state.get("message"))
                self.assertEqual(state["processed_seconds"],21600)
                self.assertEqual(len(list((basedir/"reports").glob("Часть_*.docx"))),18)
                self.assertEqual(len(list((basedir/"chunks").glob("*.json"))),180)
                self.assertTrue(first_report_before_complete and all(first_report_before_complete))
                self.assertTrue((basedir/"reports"/"Полный_отчёт.docx").is_file())


if __name__=="__main__": unittest.main()
