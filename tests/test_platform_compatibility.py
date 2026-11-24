"""Cross-platform release checks which do not need any expensive ML model."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from check_ci_platform import validate

ROOT = Path(__file__).resolve().parents[1]

class RunnerChecks(unittest.TestCase):
    def test_windows_x64_runner_accepts_amd64(self):
        self.assertIn('OK', validate('Windows-x64', 'Windows', 'AMD64'))
    def test_m4_runner_accepts_arm64(self):
        self.assertIn('OK', validate('Mac-Apple-Silicon', 'Darwin', 'arm64'))
    def test_macos_intel_cannot_be_mislabeled_m4(self):
        with self.assertRaises(RuntimeError):
            validate('Mac-Apple-Silicon', 'Darwin', 'x86_64')
    def test_windows_arm_cannot_be_mislabeled_x64(self):
        with self.assertRaises(RuntimeError):
            validate('Windows-x64', 'Windows', 'ARM64')
    def test_linux_not_claimed_as_public_installer(self):
        with self.assertRaises(ValueError):
            validate('Linux-x64', 'Linux', 'x86_64')
    def test_matrix_invokes_gate_before_big_models(self):
        s=(ROOT/'.github/workflows/build.yml').read_text(encoding='utf-8')
        check=s.index('Verify actual runner OS and architecture BEFORE costly setup')
        install=s.index('Install build dependencies')
        models=s.index('Download and checksum BOTH models')
        self.assertLess(check, install)
        self.assertLess(check, models)
        self.assertIn('Mac-Apple-Silicon', s)
        self.assertIn('Windows-x64', s)
    def test_macos_arm_binary_check_and_dmg_verify_preserved(self):
        s=(ROOT/'.github/workflows/build.yml').read_text(encoding='utf-8')
        self.assertIn('macos_bundle_audit.py', s)
        self.assertIn('scripts/macos_dmg.py --verify', s)
        self.assertIn('--smoke-gui', s)
        self.assertIn('check_release_assets.py', s)
    def test_both_platforms_ship_same_word_generator(self):
        s=(ROOT/'.github/workflows/build.yml').read_text(encoding='utf-8')
        self.assertIn('--paths src', s)
        self.assertIn('--collect-all transformers', s)
        self.assertIn('--add-data \'web:web\'', s)

if __name__ == '__main__': unittest.main()
