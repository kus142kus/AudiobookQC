"""Ensure an opt-in architecture-specific experiment can't change stable release."""
import unittest
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parents[1]

class ArmPreviewSafety(unittest.TestCase):
    def test_opt_in_aarch64_runner_is_not_publication(self):
        path=ROOT/'.github/workflows/linux-arm64-preview.yml'
        text=path.read_text('utf-8')
        self.assertIn('workflow_dispatch:',text)
        self.assertNotIn('branches: [main]',text)
        self.assertIn('runs-on: ubuntu-24.04-arm',text)
        self.assertIn('test "$(uname -m)" = aarch64',text)
        self.assertIn('xvfb-run -a python scripts/check_bundle.py',text)
        self.assertIn('AQC-Linux-ARM64-PREVIEW',text)
        self.assertNotIn('softprops/action-gh-release',text)
        self.assertNotIn('publish_beta:',text)
        self.assertIsInstance(yaml.safe_load(text),dict)

    def test_main_workflow_is_unchanged_in_public_platform_scope(self):
        text=(ROOT/'.github/workflows/build.yml').read_text('utf-8')
        self.assertIn('target: Windows-x64',text)
        self.assertIn('target: Mac-Apple-Silicon',text)
        self.assertNotIn('target: Linux-ARM64',text)
        self.assertNotIn('target: Linux-x64',text)
        self.assertNotIn('target: Windows-ARM64',text)
        self.assertNotIn('target: Mac-Intel',text)

if __name__ == '__main__': unittest.main()
