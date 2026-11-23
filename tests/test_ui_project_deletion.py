"""Tests for safely managing saved desktop projects (no GUI or model needed)."""
from __future__ import annotations
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from audiobook_qc import worker
from audiobook_qc.desktop import delete_saved_project

class DesktopProjectDeletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.root_patch = patch.object(worker,'ROOT',self.root)
        self.root_patch.start()
        self.pid = 'b'*32
        job = {'id':self.pid,'audio_name':'a.mp3','status':'paused','parts':[]}
        worker.save_job(job)
        files = worker.job_dir(self.pid) / 'inputs'
        files.mkdir()
        (files / 'audio.mp3').write_bytes(b'audio')
        self.original = self.root / 'original.mp3'
        self.original.write_bytes(b'original')
    def tearDown(self):
        worker.ACTIVE.discard(self.pid)
        self.root_patch.stop()
        self.tmp.cleanup()
    def test_delete_local_project_does_not_touch_original(self):
        delete_saved_project(self.pid)
        self.assertFalse(worker.job_dir(self.pid).exists())
        self.assertTrue(self.original.exists())
    def test_refuses_active_recognition(self):
        worker.ACTIVE.add(self.pid)
        with self.assertRaisesRegex(RuntimeError,'приостановите'):
            delete_saved_project(self.pid)
        self.assertTrue(worker.job_dir(self.pid).exists())
    def test_refuses_working_state_even_if_thread_disappeared(self):
        job=worker.load_job(self.pid)
        job['status']='running'
        worker.save_job(job)
        with self.assertRaisesRegex(RuntimeError,'остановки'):
            delete_saved_project(self.pid)
    def test_rejects_path_traversal(self):
        with self.assertRaises(ValueError):
            delete_saved_project('../original')
        self.assertTrue(self.original.exists())
    def test_refuses_linked_project_folder(self):
        linkid='c'*32
        p=worker.job_dir(linkid)
        try:
            p.symlink_to(worker.job_dir(self.pid), target_is_directory=True)
        except (OSError,NotImplementedError):
            self.skipTest('Symlinks unavailable')
        with self.assertRaisesRegex(RuntimeError,'путь'):
            delete_saved_project(linkid)
        self.assertTrue(worker.job_dir(self.pid).exists())

if __name__ == '__main__':
    unittest.main()
