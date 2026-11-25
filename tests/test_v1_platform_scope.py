"""Guard production platforms against experimental workflow regressions."""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]

class PlatformScopeTests(unittest.TestCase):
    def test_linux_preview_is_independent_and_opt_in(self):
        text=(ROOT/'.github/workflows/linux-preview.yml').read_text(encoding='utf-8')
        self.assertIn('workflow_dispatch:',text)
        self.assertNotIn('branches: [main]',text)
        self.assertIn('runs-on: ubuntu-24.04',text)
        self.assertIn('scripts/check_bundle.py',text)
        self.assertIn('xvfb-run -a',text)
        self.assertNotIn('softprops/action-gh-release',text)

    def test_production_release_remains_windows_and_apple_silicon_only(self):
        text=(ROOT/'.github/workflows/build.yml').read_text(encoding='utf-8')
        self.assertIn('target: Windows-x64',text)
        self.assertIn('target: Mac-Apple-Silicon',text)
        self.assertNotIn('target: Linux-x64',text)
        self.assertIn('python scripts/build_installer.py --test-inno',text)
        self.assertIn('python scripts/macos_dmg.py --verify',text)
        self.assertIn('prerelease: true',text)
        self.assertIn('default: false',text)

if __name__=='__main__':
    unittest.main()
