"""Regression for missing python-docx templates in frozen Windows/Mac/Linux."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from docx.parts.hdrftr import FooterPart, HeaderPart

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from run import _install_frozen_macos_docx_recovery, _verify_word_export  # noqa: E402


def _missing(name: str):
    def raise_missing(cls):
        raise FileNotFoundError(2, 'Template deliberately absent for regression test',
                                f'/nonexistent/docx/templates/{name}')
    return classmethod(raise_missing)


class FrozenDocxRecoveryTests(unittest.TestCase):
    def test_missing_header_and_footer_do_not_break_frozen_reports_on_any_os(self):
        # The real bug passed on macOS but failed on Linux.  Simulate a frozen
        # executable for every target and remove both templates deliberately.
        for platform in ('linux', 'darwin', 'win32'):
            with self.subTest(platform=platform), TemporaryDirectory() as empty_bundle:
                with patch.object(sys, 'platform', platform), \
                     patch.object(sys, 'frozen', True, create=True), \
                     patch.object(sys, '_MEIPASS', empty_bundle, create=True), \
                     patch.object(HeaderPart, '_aqc_missing_template_recovery', False, create=True), \
                     patch.object(FooterPart, '_aqc_missing_template_recovery', False, create=True), \
                     patch.object(HeaderPart, '_default_header_xml', _missing('default-header.xml')), \
                     patch.object(FooterPart, '_default_footer_xml', _missing('default-footer.xml')):
                    _install_frozen_macos_docx_recovery()
                    self.assertTrue(HeaderPart._aqc_missing_template_recovery)
                    self.assertTrue(FooterPart._aqc_missing_template_recovery)
                    with patch('run._stage') as stage:
                        _verify_word_export()
                        stage.assert_any_call('AUDIOBOOK_QC_WORD_REPORT_EXPORT_OK')

    def test_no_patch_is_installed_in_normal_source_mode(self):
        with patch.object(sys, 'frozen', False, create=True), \
             patch.object(HeaderPart, '_aqc_missing_template_recovery', False, create=True):
            _install_frozen_macos_docx_recovery()
            self.assertFalse(HeaderPart._aqc_missing_template_recovery)


if __name__ == '__main__':
    unittest.main()
