import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from macos_bundle_audit import audit, candidate_binaries

class MacBundleAuditTests(unittest.TestCase):
    def create_app(self, folder: Path):
        app=folder/'AQC.app'
        (app/'Contents'/'MacOS').mkdir(parents=True)
        (app/'Contents'/'Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable':'AQC'}))
        (app/'Contents'/'MacOS'/'AQC').write_bytes(b'not-a-real-mach-o')
        return app

    def test_app_contains_framework_binaries_in_audit_scope(self):
        with tempfile.TemporaryDirectory() as td:
            app=self.create_app(Path(td))
            f=app/'Contents'/'Frameworks'/'Widget.framework'/'Widget'
            f.parent.mkdir(parents=True)
            f.write_bytes(b'not-a-real-mach-o')
            names=set(candidate_binaries(app,app/'Contents'/'MacOS'/'AQC'))
            self.assertIn(f,names)

    def test_wrong_macho_cpu_fails_the_release_gate(self):
        with tempfile.TemporaryDirectory() as td:
            app=self.create_app(Path(td))
            def fake_run(args, **kwargs):
                if args[0]=='file': return SimpleNamespace(stdout='Mach-O executable arm64')
                return SimpleNamespace(stdout='x86_64\n')
            with patch('macos_bundle_audit.subprocess.run',side_effect=fake_run):
                with self.assertRaisesRegex(RuntimeError, 'Нет arm64'):
                    audit(app)

    def test_correct_macho_cpu_passes_gate(self):
        with tempfile.TemporaryDirectory() as td:
            app=self.create_app(Path(td))
            def fake_run(args, **kwargs):
                if args[0]=='file': return SimpleNamespace(stdout='Mach-O executable arm64')
                return SimpleNamespace(stdout='arm64\n')
            with patch('macos_bundle_audit.subprocess.run',side_effect=fake_run):
                audit(app)

if __name__ == '__main__': unittest.main()
