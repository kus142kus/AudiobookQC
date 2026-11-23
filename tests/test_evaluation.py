import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from audiobook_qc.evaluation import score_findings
class BenchmarkTests(unittest.TestCase):
    def test_precision_recall_one_to_one_with_duplicate_false_alarm(self):
        t=[{'kind':'repeat','start':10},{'kind':'replacement','start':50}]
        f=[{'kind':'repeat','start':11},{'kind':'repeat','start':11.5},
           {'kind':'replacement','start':58}]
        s=score_findings(t,f)
        self.assertEqual(s['total']['tp'],1)
        self.assertEqual(s['total']['fp'],2)
        self.assertEqual(s['total']['fn'],1)
        self.assertAlmostEqual(s['total']['precision'],1/3)
        self.assertAlmostEqual(s['total']['recall'],.5)
    def test_empty_truth_and_findings(self):
        self.assertIsNone(score_findings([],[])['total']['precision'])
    def test_negative_tolerance_rejected(self):
        with self.assertRaises(ValueError):score_findings([],[], -1)
