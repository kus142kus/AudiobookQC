"""Regression tests for the frozen Windows YAMNet failure.

These are intentionally executed before downloading any model weights.
The real ONNX forward pass is separately tested by GitHub Actions.
"""
from __future__ import annotations
import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np
import run
from audiobook_qc.events import AudioEventDetector


class CountingFakeSession:
    class Input:
        name = "waveform"

    def __init__(self):
        self.calls = 0
        self.durations = []

    def get_inputs(self):
        return [self.Input()]

    def run(self, unused, feeds):
        self.calls += 1
        signal = feeds['waveform']
        self.durations.append(len(signal))
        assert float(np.max(np.abs(signal))) > .01, 'CI test must be audible'
        return [np.zeros((4, 2), dtype=np.float32)]


class RealEntrypointTests(unittest.TestCase):
    def test_event_smoke_uses_wave_import_and_executes_inference(self):
        session = CountingFakeSession()
        detector = AudioEventDetector(session=session, classes=['Speech', 'Cough'])
        # Run the exact production CLI flag branch, with just the ONNX session
        # mocked. This would have caught the missing `wave` import.
        with patch('audiobook_qc.events.AudioEventDetector', return_value=detector):
            run.single_model_test('events')
        self.assertGreater(session.calls, 0)
        self.assertTrue(all(n > 16000 for n in session.durations))

    def test_non_silent_smoke_pcm_is_three_seconds(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'event.wav'
            run._audible_test_wav(path)
            with wave.open(str(path), 'rb') as wav:
                self.assertEqual(wav.getnframes(), 48000)
                self.assertEqual(wav.getnchannels(), 1)
                self.assertEqual(wav.getframerate(), 16000)
                data = np.frombuffer(wav.readframes(48000), dtype='<i2')
                self.assertGreater(int(np.max(np.abs(data))), 1000)

    def test_frozen_smoke_failure_is_logged_and_exits_without_dialog(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / 'progress.txt'
            with patch.dict(os.environ, {'AUDIOBOOK_QC_SMOKE_LOG': str(log)}), \
                 patch.object(run, 'single_model_test', side_effect=ValueError('inference failed')):
                code = run.main(['--test-events'])
            self.assertEqual(code, 1)
            text = log.read_text(encoding='utf-8')
            self.assertIn('AUDIOBOOK_QC_SMOKE_TEST_FAILED', text)
            self.assertIn('ValueError: inference failed', text)

    def test_windowed_app_without_stdout_still_writes_failure_log(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / 'windowed.txt'
            with patch.dict(os.environ, {'AUDIOBOOK_QC_SMOKE_LOG': str(log)}), \
                 patch.object(run, 'single_model_test', side_effect=RuntimeError('bundle error')), \
                 patch.object(run.sys, 'stdout', None), \
                 patch.object(run.sys, 'stderr', None):
                code = run.main(['--test-events'])
            self.assertEqual(code, 1)
            self.assertIn('RuntimeError: bundle error', log.read_text(encoding='utf-8'))

    def test_no_model_is_ever_reported_as_success(self):
        class FailingSession(CountingFakeSession):
            def run(self, *args, **kwargs):
                raise RuntimeError('ONNX provider failed')
        detector = AudioEventDetector(session=FailingSession(), classes=['Speech', 'Cough'])
        with patch('audiobook_qc.events.AudioEventDetector', return_value=detector):
            with self.assertRaisesRegex(RuntimeError, 'ONNX provider failed'):
                run.single_model_test('events')


if __name__ == '__main__':
    unittest.main()
