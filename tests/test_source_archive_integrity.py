"""Source archives must never omit .github or include the user's audiobook."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('aqc_source_audit',ROOT/'scripts/audit_source_archive.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class SourceArchiveGuards(unittest.TestCase):
    def test_missing_hidden_workflow_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'incomplete.zip'
            with ZipFile(path,'w') as f:
                f.writestr('AudiobookQC/run.py','pass')
            with self.assertRaisesRegex(ValueError,'hidden \\.github'):
                module.audit(path)

    def test_private_recording_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'user_audio.zip'
            with ZipFile(path,'w') as f:
                for n in module.EXPECTED: f.writestr(n,'x')
                f.writestr('AudiobookQC/16_01.mp3','private book')
            with self.assertRaisesRegex(ValueError,'Private media'):
                module.audit(path)

    def test_bad_entry_refused(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'unsafe.zip'
            with ZipFile(path,'w') as f:
                for n in module.EXPECTED: f.writestr(n,'x')
                f.writestr('AudiobookQC/../../oops.txt','bad')
            with self.assertRaisesRegex(ValueError,'Unsafe ZIP'):
                module.audit(path)

if __name__=='__main__':unittest.main()
