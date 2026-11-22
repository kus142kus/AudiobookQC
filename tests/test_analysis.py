import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from audiobook_qc.analysis import compare, tokenize, fmt_time
from audiobook_qc.report import generate_report
from audiobook_qc.worker import write_json, read_json


class AlignmentTests(unittest.TestCase):
    def sample(self, said):
        return [{"word": w, "start": i*.5, "end": i*.5+.4} for i,w in enumerate(tokenize(said))]

    def test_repeat_is_visible(self):
        book = ("Тогда он медленно открыл дверь и увидел старого друга. После этого "
                "он отправился домой и долго думал о своей судьбе. "
                "Наконец наступило новое утро и все снова вышли из дома.")
        said = ("Тогда он медленно открыл дверь и увидел старого друга. После этого "
                "он отправился домой он отправился домой и долго думал о своей судьбе. "
                "Наконец наступило новое утро и все снова вышли из дома.")
        issues, cursor, q = compare(tokenize(book),self.sample(said))
        self.assertTrue(any(x["kind"] == "repeat" for x in issues), issues)
        self.assertGreater(q["matched"], .75)
        self.assertGreater(cursor, 20)

    def test_semantic_replacement(self):
        book = ("Когда наступил вечер собрались друзья и стали читать книгу. "
                "Было ясно что нам не дано предугадать будущее и не стоит торопиться. "
                "На следующий день они снова встретились и продолжили разговор.")
        said = book.replace("не дано предугадать", "не надо предугадать")
        issues, _, _ = compare(tokenize(book), self.sample(said))
        self.assertTrue(any(x["kind"] == "replacement" and "надо" in x["spoken"] for x in issues), issues)

    def test_no_false_errors_on_identical_text(self):
        book = "Однажды в далёком лесу жили мудрые люди которые любили читать книги и рассказывать истории"
        issues, _, quality = compare(tokenize(book), self.sample(book))
        self.assertEqual(issues, [])
        self.assertGreater(quality["matched"], .95)

    def test_bad_alignment_does_not_spam_typos(self):
        book = "Слова о русской истории о событиях давно минувших лет и людях которые помогли стране стать лучше"
        said = "Вокруг летели красивые корабли и мимо шли три поезда затем поднялся огромный воздушный шар"
        issues, cursor, q = compare(tokenize(book), self.sample(said))
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["kind"], "uncertain")
        self.assertTrue(issues[0]["id"])
        self.assertEqual(cursor, 0)

    def test_timestamp_over_six_hours(self):
        self.assertEqual(fmt_time(6*3600+13*60+3), "06:13:03")

    def test_word_report_generated_and_readable(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"Часть_001.docx"
            issue = {"id":"one", "kind":"repeat", "severity":"high", "start":123.0,
                     "end":128.0,"spoken":"мы снова повторяем", "expected":"мы повторяем",
                     "reason":"Вероятный повтор"}
            generate_report(path, {"audio_name":"book.mp3","text_name":"book.docx"},
                            [issue], 0, 1200, reviews={"one":"fixed"})
            from docx import Document
            doc=Document(path)
            all_words=' '.join(cell.text for t in doc.tables for row in t.rows for cell in row.cells)
            self.assertIn("мы снова повторяем", all_words)
            self.assertIn("Исправлено", all_words)
            self.assertIn("00:02:03", all_words)


if __name__ == "__main__":
    unittest.main()
