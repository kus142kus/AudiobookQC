"""Regression coverage for the real Windows user crash in pythonnet WinForms."""
from __future__ import annotations

import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from audiobook_qc import desktop_web


class _Event:
    def __init__(self, loaded=True):
        self.loaded = loaded
    def wait(self, timeout):
        return self.loaded
    def __iadd__(self, callback):
        return self


class FakeWindow:
    def __init__(self, js_ready=True, loaded=True):
        self.events = types.SimpleNamespace(loaded=_Event(loaded), closed=_Event())
        self.js_ready = js_ready
        self.destroyed = False
    def evaluate_js(self, code):
        assert 'projectView' in code and 'pywebview' in code
        return self.js_ready
    def destroy(self):
        self.destroyed = True


class NativeGuiBackendTests(unittest.TestCase):
    def test_native_windows_uses_qt_not_dotnet(self):
        self.assertEqual(desktop_web.preferred_gui_backend('win32'), 'qt')
        self.assertIsNone(desktop_web.preferred_gui_backend('darwin'))
        self.assertEqual(desktop_web.preferred_gui_backend('linux'), 'qt')

    def test_qt_integrity_probe_needs_local_webengine(self):
        qt_core = types.ModuleType('PySide6.QtCore'); qt_core.QLibraryInfo = object()
        qt_widgets = types.ModuleType('PySide6.QtWebEngineWidgets'); qt_widgets.QWebEngineView = object()
        qt_channel = types.ModuleType('PySide6.QtWebChannel'); qt_channel.QWebChannel = object()
        with patch.object(desktop_web.sys, 'platform', 'win32'), patch.dict(
            sys.modules,
            {'PySide6':types.ModuleType('PySide6'), 'PySide6.QtCore':qt_core,
             'PySide6.QtWebEngineWidgets':qt_widgets, 'PySide6.QtWebChannel':qt_channel}
        ):
            self.assertEqual(desktop_web.verify_gui_backend(), 'qt')
            self.assertEqual(os.environ.get('QT_API'), 'pyside6')

    def test_qt_probe_fails_if_engine_missing_no_fallback_to_winforms(self):
        with patch.object(desktop_web.sys, 'platform', 'win32'):
            with patch.dict(sys.modules, {'PySide6':None}):
                with self.assertRaisesRegex(RuntimeError, 'Qt WebEngine'):
                    desktop_web.verify_gui_backend()

    def test_window_uses_selected_qt_backend(self):
        window = FakeWindow()
        fake_webview=types.ModuleType('webview')
        fake_webview.create_window=MagicMock(return_value=window)
        fake_webview.start=MagicMock()
        with patch.object(desktop_web, 'offline_environment'), patch.object(desktop_web, 'Bridge'), \
             patch.object(desktop_web, 'verify_gui_backend', return_value='qt'), \
             patch.object(desktop_web, 'preferred_gui_backend', return_value='qt'), \
             patch.object(desktop_web, 'bundled_html', return_value='<h1>test</h1>'), \
             patch.dict(sys.modules, {'webview':fake_webview}):
            desktop_web.app_window()
        self.assertEqual(fake_webview.start.call_args.kwargs['gui'],'qt')
        self.assertFalse(fake_webview.start.call_args.kwargs['http_server'])

    def test_packaged_gui_smoke_cannot_pass_without_real_dom_and_bridge(self):
        for valid in (True, False):
            with self.subTest(valid=valid):
                window = FakeWindow(js_ready=valid)
                fake_webview=types.ModuleType('webview')
                fake_webview.create_window=MagicMock(return_value=window)
                def run_fake(func, **kwargs):
                    func()
                fake_webview.start=MagicMock(side_effect=run_fake)
                with patch.object(desktop_web, 'offline_environment'), patch.object(desktop_web, 'Bridge'), \
                    patch.object(desktop_web, 'verify_gui_backend', return_value='qt'), \
                    patch.object(desktop_web, 'preferred_gui_backend', return_value='qt'), \
                    patch.object(desktop_web, 'bundled_html', return_value='<h1>test</h1>'), \
                    patch.dict(sys.modules, {'webview':fake_webview}), \
                    patch.object(desktop_web, 'threading'):
                    if valid:
                        desktop_web.app_window(smoke=True)
                    else:
                        # Avoid a 12s polling delay in an intentionally failed test.
                        with patch('time.sleep',return_value=None):
                            with self.assertRaisesRegex(RuntimeError, 'bridge missing'):
                                desktop_web.app_window(smoke=True)
                self.assertTrue(window.destroyed)

    def test_build_runs_gui_preflight_before_gb_download(self):
        workflow=(Path(__file__).parents[1]/'.github'/'workflows'/'build.yml').read_text(encoding='utf-8')
        i_gui=workflow.index('Exercise native desktop renderer BEFORE downloading models')
        i_download=workflow.index('Download and checksum BOTH models')
        self.assertLess(i_gui,i_download)
        self.assertIn('--exclude-module pythonnet',workflow)
        self.assertIn('--hidden-import PySide6.QtWebEngineWidgets',workflow)
        self.assertIn('--exclude-module webview.platforms.winforms',workflow)

    def test_frozen_gui_smoke_runs_before_model_inference(self):
        smoke=(Path(__file__).parents[1]/'scripts'/'check_bundle.py').read_text(encoding='utf-8')
        self.assertLess(smoke.index('(("--smoke-gui", 120)'), smoke.index('("--self-test", 180)'))


if __name__ == '__main__':
    unittest.main()
