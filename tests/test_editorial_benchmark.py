"""Quality measurement safeguards — no model downloads and no personal recordings."""
from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('benchmark_readings', ROOT/'scripts/benchmark_readings.py')
import sys
sys.modules[spec.name] = mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class AccuracyBenchmarkTests(unittest.TestCase):
    def F(self, t, kind='replacement', importance='high'):
        return mod.Finding(t, kind, importance=importance)

    def test_6_hours_absolute_timestamps(self):
        self.assertEqual(mod.seconds('06:12:31'), 22351)
        self.assertEqual(mod.hhmmss(22351), '06:12:31')
        self.assertEqual(mod.seconds('00:00:02,250'), 2.25)
        self.assertEqual(mod.seconds('02:03'), 123)

    def test_invalid_timestamp_must_not_be_silently_rounded(self):
        for value in ('00:60:01', '00:10:60', '-3', '6:88', 'garbage'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                mod.seconds(value)

    def test_empty_clean_recording_is_not_reported_as_having_errors(self):
        result = mod.evaluate([], [], duration_hours=0.5)
        self.assertEqual(result['totals']['true_positive'], 0)
        self.assertEqual(result['totals']['false_positive'], 0)
        self.assertEqual(result['totals']['false_negative'], 0)
        self.assertEqual(result['precision'], 1.0)

    def test_app_misses_everything(self):
        result = mod.evaluate([self.F(50)], [], duration_hours=1)
        self.assertEqual(result['precision'], 0)
        self.assertEqual(result['recall'], 0)
        self.assertEqual(len(result['missed']), 1)

    def test_duplicate_findings_do_not_count_twice(self):
        result = mod.evaluate([self.F(60)], [self.F(60), self.F(61)])
        self.assertEqual(result['totals']['true_positive'], 1)
        self.assertEqual(result['totals']['false_positive'], 1)
        self.assertEqual(result['precision'], .5)

    def test_one_to_one_matching_can_reassign_nearby_predictions(self):
        # Greedy nearest-neighbour would leave one truth unmatched here.
        result = mod.evaluate([self.F(0), self.F(6)], [self.F(3), self.F(0)], tolerance=3)
        self.assertEqual(result['totals']['true_positive'], 2)
        self.assertEqual(result['totals']['false_negative'], 0)

    def test_distant_false_alarm_not_confirmed(self):
        result = mod.evaluate([self.F(60)], [self.F(66)], tolerance=5)
        self.assertEqual(result['totals']['true_positive'], 0)
        self.assertEqual(result['totals']['false_positive'], 1)
        self.assertEqual(result['totals']['false_negative'], 1)

    def test_strict_kind_prevents_bogus_category_success(self):
        truth = [self.F(10, 'negation')]
        predictions = [self.F(10, 'replacement')]
        self.assertEqual(mod.evaluate(truth, predictions, strict_kind=True)['recall'], 0)
        self.assertEqual(mod.evaluate(truth, predictions, strict_kind=False)['recall'], 1)

    def test_minor_findings_excluded_unless_explicitly_enabled(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'findings.csv'
            with p.open('w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=['time','kind','importance'])
                writer.writeheader()
                writer.writerow(dict(time='00:01:00', kind='replacement', importance='high'))
                writer.writerow(dict(time='00:01:10', kind='sound', importance='review'))
            main, skipped = mod.load_csv(p)
            self.assertEqual((len(main), skipped), (1, 1))
            all_, skipped = mod.load_csv(p, include_minor=True)
            self.assertEqual((len(all_), skipped), (2, 0))

    def test_unverified_findings_are_not_promoted_to_proven(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'extracted.csv'
            p.write_text('time,kind,importance\n00:10:00,replacement,review\n', encoding='utf-8')
            findings, ignored = mod.load_csv(p)
            self.assertEqual(findings, [])
            self.assertEqual(ignored, 1)

    def test_invalid_severity_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'invalid.csv'
            p.write_text('time,importance\n00:01:02,absolutely-certain-AI\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'unknown importance'):
                mod.load_csv(p)

    def test_csv_reports_and_word_report(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            truth = d/'truth.csv'
            found = d/'found.csv'
            truth.write_text('time,kind,expected,heard,importance\n'
                             '00:01:20,замена,кошка,ложка,high\n'
                             '00:02:20,пропуск,очень важный,нет,high\n', encoding='utf-8')
            found.write_text('time,kind,expected,heard,importance\n'
                             '00:01:22,замена,кошка,ложка,high\n'
                             '00:04:00,замена,дом,том,high\n', encoding='utf-8')
            output = d/'summary.json'
            report = d/'summary.docx'
            self.assertEqual(mod.main(['--truth',str(truth), '--findings',str(found),
                                       '--output',str(output), '--docx',str(report),
                                       '--duration-hours','0.1']), 0)
            stats = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(stats['precision'], 0.5)
            self.assertEqual(stats['recall'], 0.5)
            self.assertEqual(stats['totals']['false_negative'], 1)
            from docx import Document
            doc = Document(report)
            self.assertTrue(doc.tables)
            self.assertIn('00:02:20', doc.tables[0].rows[1].cells[0].text)

    def test_threshold_gate_fails_when_accuracy_is_low(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d/'truth.csv').write_text('time,importance\n00:01:00,high\n',encoding='utf-8')
            (d/'found.csv').write_text('time,importance\n00:02:00,high\n',encoding='utf-8')
            self.assertEqual(mod.main(['--truth',str(d/'truth.csv'),
                                       '--findings',str(d/'found.csv'),
                                       '--output',str(d/'summary.json'),
                                       '--min-precision','0.9']), 2)
            self.assertTrue((d/'summary.json').exists())

    def test_extract_word_rows_marked_review_not_confirmed(self):
        from docx import Document
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            doc = Document()
            table = doc.add_table(rows=1, cols=4)
            for cell, text in zip(table.rows[0].cells,
                                  ('Таймкод', 'По тексту', 'Распознано', 'Что проверить')):
                cell.text = text
            for cell, text in zip(table.add_row().cells,
                                  ('06:14:32', 'я вернулся', 'я отвернулся', 'замена')):
                cell.text = text
            source = d/'report.docx'
            dest = d/'extracted.csv'
            doc.save(source)
            self.assertEqual(mod.extract_docx(source,dest),1)
            data, ignored = mod.load_csv(dest)
            self.assertEqual((data, ignored), ([], 1))
            data, ignored = mod.load_csv(dest, include_minor=True)
            self.assertEqual(data[0].time, 6*3600+14*60+32)


if __name__ == '__main__':
    unittest.main()
