"""Mac DMG release gate tests; native hdiutil still runs on GitHub's macOS runner."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('macos_dmg', ROOT/'scripts/macos_dmg.py')
dmg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dmg)


def udif_file(path: Path, payload=b'TEST'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload + b'\x00' * 1024 + b'koly' + b'\x00' * 508)


def fake_app(path: Path):
    app = path / 'AudiobookQC.app'
    plist = app/'Contents/Info.plist'
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(plistlib.dumps({'CFBundleExecutable': 'AudiobookQC'}))
    binary = app/'Contents/MacOS/AudiobookQC'
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b'fake Mach-O')
    return app


class DmgRobustnessTests(unittest.TestCase):
    def test_github_workflow_uses_true_native_verification_and_mount(self):
        text = (ROOT/'.github/workflows/build.yml').read_text(encoding='utf-8')
        self.assertIn('macos_dmg.py --package', text)
        self.assertIn('macos_dmg.py --verify', text)
        self.assertLess(text.index('macos_dmg.py --package'), text.index('macos_dmg.py --verify'))
        self.assertLess(text.index('macos_dmg.py --verify'), text.index('check_release_assets.py'))
        self.assertNotIn('hdiutil verify dist/AudiobookQC-Mac.dmg', text)

    def test_retry_only_transient_hdiutil_resource_errors(self):
        messages = ['Resource temporarily unavailable', 'Resource busy',
                    'Device busy', 'resource is busy']
        for message in messages:
            with self.subTest(message=message):
                self.assertTrue(dmg.is_transient_error(message))
        for message in ('image not recognized', 'checksum mismatch',
                        'No such file or directory', 'Bad CRC'):
            with self.subTest(message=message):
                self.assertFalse(dmg.is_transient_error(message))

    def test_transient_verify_retries_then_success(self):
        responses = [subprocess.CompletedProcess([], 1, '', 'Resource temporarily unavailable'),
                     subprocess.CompletedProcess([], 1, '', 'Resource busy'),
                     subprocess.CompletedProcess([], 0, 'verified OK', '')]
        seen, slept = [], []
        def runner(cmd, **kwargs):
            seen.append(cmd)
            return responses.pop(0)
        dmg.run_hdiutil(['verify', '/tmp/example.dmg'], runner=runner,
                        sleeper=slept.append, attempts=4)
        self.assertEqual(len(seen), 3)
        self.assertEqual(slept, [5,10])

    def test_permanent_verify_corruption_does_not_retry(self):
        seen = []
        def runner(cmd, **kwargs):
            seen.append(cmd)
            return subprocess.CompletedProcess([], 1, '', 'image not recognized: invalid checksum')
        with self.assertRaisesRegex(dmg.DiskImageError, 'invalid checksum'):
            dmg.run_hdiutil(['verify', 'damaged.dmg'], runner=runner,
                            sleeper=lambda _: None)
        self.assertEqual(len(seen), 1)

    def test_unrecovered_transient_does_not_pass_build(self):
        attempts = []
        def runner(cmd, **kwargs):
            attempts.append(1)
            return subprocess.CompletedProcess([], 1, '', 'Resource temporarily unavailable')
        with self.assertRaisesRegex(dmg.DiskImageError, 'attempt 3/3'):
            dmg.run_hdiutil(['verify', 'unstable.dmg'], runner=runner,
                            sleeper=lambda _: None, attempts=3)
        self.assertEqual(len(attempts), 3)

    def test_undamaged_looking_fake_image_without_udif_footer_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp)/'bad.dmg'
            bad.write_bytes(b'X' * 4096)
            with self.assertRaisesRegex(dmg.DiskImageError, 'koly'):
                dmg.verify_image(bad)

    def test_github_release_2gib_limit_checked_before_mount(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp)/'big.dmg'
            with bad.open('wb') as f:
                f.truncate(dmg.MAX_RELEASE_BYTES - 512)
                f.seek(dmg.MAX_RELEASE_BYTES - 512)
                f.write(b'koly' + b'\0'*508)
            with self.assertRaisesRegex(dmg.DiskImageError, '2 GiB'):
                dmg.verify_image(bad)

    def test_mount_layout_checks_real_executable_and_applications_shortcut(self):
        with tempfile.TemporaryDirectory() as tmp:
            mount = Path(tmp)
            fake_app(mount)
            with self.assertRaisesRegex(dmg.DiskImageError, 'shortcut'):
                dmg.verify_layout(mount)
            (mount/'Applications').symlink_to('/Applications')
            dmg.verify_layout(mount)
            (mount/'AudiobookQC.app/Contents/MacOS/AudiobookQC').unlink()
            with self.assertRaisesRegex(dmg.DiskImageError, 'missing application executable'):
                dmg.verify_layout(mount)

    def test_native_integrity_and_mount_both_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root/'test.dmg'
            udif_file(image)
            commands = []
            def runner(args, **kwargs):
                commands.append(args[0])
                if args[0] == 'attach':
                    mount = Path(args[args.index('-mountpoint') + 1])
                    fake_app(mount)
                    (mount/'Applications').symlink_to('/Applications')
                return subprocess.CompletedProcess(args, 0, 'OK', '')
            with patch.object(dmg, 'run_hdiutil', side_effect=runner):
                dmg.verify_image(image, temp_base=root)
            self.assertEqual(commands, ['verify', 'attach', 'detach'])

    def test_bad_mounted_contents_detach_and_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root/'test.dmg'
            udif_file(image)
            commands = []
            def runner(args, **kwargs):
                commands.append(args[0])
                return subprocess.CompletedProcess(args, 0, 'OK', '')
            with patch.object(dmg, 'run_hdiutil', side_effect=runner):
                with self.assertRaisesRegex(dmg.DiskImageError, 'Info.plist'):
                    dmg.verify_image(image, temp_base=root)
            self.assertEqual(commands, ['verify', 'attach', 'detach'])

    def test_package_creates_staged_app_and_atomically_published_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = fake_app(root)
            image = root/'AudiobookQC-Mac.dmg'
            seen = []
            def runner(args, **kwargs):
                seen.append(args[0])
                self.assertTrue((root/'dmg-root/AudiobookQC.app/Contents/Info.plist').exists())
                self.assertEqual((root/'dmg-root/Applications').readlink(), Path('/Applications'))
                udif_file(Path(args[-1]))
                return subprocess.CompletedProcess(args, 0, 'OK', '')
            with patch.object(dmg, 'run_hdiutil', side_effect=runner):
                dmg.package_app(app, image)
            self.assertEqual(seen, ['create'])
            self.assertTrue(image.is_file())
            self.assertFalse((root/'AudiobookQC-Mac.pending.dmg').exists())

    def test_package_never_publishes_failed_partial_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = fake_app(root)
            image = root/'AudiobookQC-Mac.dmg'
            def runner(args, **kwargs):
                Path(args[-1]).write_bytes(b'not a dmg')
                raise dmg.DiskImageError('hdiutil failed')
            with patch.object(dmg, 'run_hdiutil', side_effect=runner):
                with self.assertRaises(dmg.DiskImageError):
                    dmg.package_app(app, image)
            self.assertFalse(image.exists())
            self.assertFalse((root/'AudiobookQC-Mac.pending.dmg').exists())


if __name__ == '__main__':
    unittest.main()
