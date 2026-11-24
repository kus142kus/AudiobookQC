"""Create and validate a macOS Apple Silicon DMG on a native macOS runner.

macOS diskimages-helper sometimes returns EAGAIN ("Resource temporarily
unavailable") immediately after image creation. Only this transient failure
is retried; invalid images, missing files and checksum failures always fail.

A valid UDIF image ends in a 512-byte `koly` footer. This check alone does
not prove integrity: native hdiutil verify AND a real read-only mount are
mandatory before uploading any release asset.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time

MAX_RELEASE_BYTES = 2 * 1024**3
TRANSIENT_MESSAGES = (
    'resource temporarily unavailable', 'resource busy',
    'device busy', 'resource is busy',
)


class DiskImageError(RuntimeError):
    """Report the actual failed command and complete relevant error output."""


def is_transient_error(message: str) -> bool:
    return any(key in message.lower() for key in TRANSIENT_MESSAGES)


def run_hdiutil(args: list[str], *, attempts: int = 4, timeout: int = 600,
                runner=subprocess.run, sleeper=time.sleep):
    """Only retry known EAGAIN/EBUSY errors; never conceal genuine damage."""
    if attempts < 1:
        raise ValueError('attempts must be at least one')
    command = ['hdiutil', *[str(arg) for arg in args]]
    for attempt in range(1, attempts + 1):
        try:
            proc = runner(command, text=True, capture_output=True, timeout=timeout,
                          check=False)
        except subprocess.TimeoutExpired as exc:
            raise DiskImageError(f'hdiutil timed out after {timeout}s: {command}') from exc
        except OSError as exc:
            raise DiskImageError(f'hdiutil unavailable: {exc}') from exc
        output = (proc.stdout or '') + '\n' + (proc.stderr or '')
        if proc.returncode == 0:
            if attempt > 1:
                print(f'AQC_DMG_TRANSIENT_RECOVERED after {attempt} attempts: {args[0]}', flush=True)
            return proc
        if attempt < attempts and is_transient_error(output):
            delay = min(5 * attempt, 15)
            print(f'AQC_DMG_TRANSIENT_RETRY {attempt}/{attempts} on {args[0]}: '
                  f'{output.strip()[-500:]}', flush=True)
            sleeper(delay)
            continue
        raise DiskImageError(f'hdiutil {args[0]} failed ({proc.returncode}) '
                             f'on attempt {attempt}/{attempts}:\n{output[-6000:]}')
    raise AssertionError('unreachable')


def validate_udif_footer(image: Path) -> int:
    """Reject a zero-byte, truncated, or clearly non-DMG artifact."""
    image = Path(image)
    if not image.is_file():
        raise DiskImageError(f'DMG does not exist: {image}')
    size = image.stat().st_size
    if size < 1024:
        raise DiskImageError(f'DMG too small or truncated: {size} bytes')
    with image.open('rb') as stream:
        stream.seek(-512, os.SEEK_END)
        trailer = stream.read(512)
    if trailer[:4] != b'koly':
        raise DiskImageError(f'DMG is missing the UDIF koly footer: {image}')
    return size


def verify_layout(mount: Path) -> None:
    """Check the user-visible DMG contents, not just the archive checksum."""
    app = mount / 'AudiobookQC.app'
    plist = app / 'Contents/Info.plist'
    link = mount / 'Applications'
    if not plist.is_file():
        raise DiskImageError('Mounted DMG is missing AudiobookQC.app/Contents/Info.plist')
    data = plistlib.loads(plist.read_bytes())
    binary = data.get('CFBundleExecutable')
    if not isinstance(binary, str) or not binary or '/' in binary or '\\' in binary:
        raise DiskImageError('Invalid CFBundleExecutable in mounted app')
    if not (app / 'Contents/MacOS' / binary).is_file():
        raise DiskImageError(f'Mounted DMG is missing application executable {binary!r}')
    if not link.is_symlink() or os.readlink(link) != '/Applications':
        raise DiskImageError('Mounted DMG is missing its /Applications shortcut')
    print('AQC_DMG_MOUNT_CONTENTS_OK', flush=True)


def verify_image(image: Path, *, temp_base: Path | None = None) -> None:
    """Run hdiutil's real integrity check and mount smoke test; do not skip either."""
    image = Path(image).resolve()
    size = validate_udif_footer(image)
    if size >= MAX_RELEASE_BYTES:
        raise DiskImageError(
            f'DMG size {size / 1024**3:.3f} GiB reaches GitHub Releases 2 GiB '
            'per-file limit. Release must use smaller or split assets.')
    # Native validation must happen AFTER the file has been fully closed.
    run_hdiutil(['verify', str(image)], attempts=5, timeout=480)
    print('AQC_DMG_HDIUTIL_VERIFY_OK', flush=True)
    with tempfile.TemporaryDirectory(prefix='aqc-dmg-mount-',
                                     dir=str(temp_base) if temp_base else None) as td:
        mount = Path(td) / 'volume'
        mount.mkdir()
        attached = False
        error = None
        try:
            run_hdiutil(['attach', '-readonly', '-nobrowse', '-mountpoint',
                         str(mount), str(image)], attempts=5, timeout=180)
            attached = True
            verify_layout(mount)
        except Exception as exc:
            error = exc
            raise
        finally:
            if attached:
                try:
                    run_hdiutil(['detach', str(mount)], attempts=5, timeout=120)
                except Exception as detach_error:
                    if error is not None:
                        print('AQC_DMG_DETACH_ALSO_FAILED: ' + str(detach_error),
                              file=sys.stderr, flush=True)
                    else:
                        raise
    print('AQC_DMG_VERIFIED_AND_MOUNTED_OK', flush=True)


def package_app(app: Path, image: Path) -> Path:
    """Create a fresh compressed image, never publish partial output."""
    app, image = Path(app).resolve(), Path(image).resolve()
    if not app.is_dir() or app.suffix != '.app':
        raise DiskImageError(f'Expected a built .app bundle: {app}')
    if not (app / 'Contents/Info.plist').is_file():
        raise DiskImageError('Built .app has no Contents/Info.plist')
    if image.name != 'AudiobookQC-Mac.dmg':
        raise DiskImageError('Unexpected output DMG filename')
    image.parent.mkdir(parents=True, exist_ok=True)
    if app.is_relative_to(image.parent / 'dmg-root'):
        raise DiskImageError('Do not pass the staged app to the packaging step')
    stage = image.parent / 'dmg-root'
    if stage.exists():
        raise DiskImageError(f'Existing DMG staging directory; refusing overwrite: {stage}')
    app_size = sum(f.stat().st_size for f in app.rglob('*') if f.is_file() and not f.is_symlink())
    free = shutil.disk_usage(image.parent).free
    required = max(1024**3, app_size // 2 + 512 * 1024**2)
    if free < required:
        raise DiskImageError(f'Insufficient free disk for DMG: {free/1024**3:.2f} GiB '
                             f'available, require at least {required/1024**3:.2f} GiB')
    stage.mkdir()
    shutil.move(str(app), str(stage / 'AudiobookQC.app'))
    (stage / 'Applications').symlink_to('/Applications', target_is_directory=True)
    pending = image.with_name('AudiobookQC-Mac.pending.dmg')
    # Only these two generated output paths are touched; never delete the source.
    pending.unlink(missing_ok=True)
    image.unlink(missing_ok=True)
    try:
        try:
            run_hdiutil(['create', '-volname', 'Audiobook QC Offline',
                         '-srcfolder', str(stage), '-format', 'UDZO',
                         '-ov', str(pending)], attempts=4, timeout=1200)
        except DiskImageError:
            pending.unlink(missing_ok=True)
            raise
        validate_udif_footer(pending)
        os.replace(pending, image)
    finally:
        pending.unlink(missing_ok=True)
    print(f'AQC_DMG_CREATED {image} ({image.stat().st_size / 1024**2:.1f} MiB)',
          flush=True)
    return image


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Native macOS DMG packaging and real verification')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--package', action='store_true')
    group.add_argument('--verify', action='store_true')
    parser.add_argument('--app', type=Path, default=Path('dist/AudiobookQC.app'))
    parser.add_argument('--dmg', type=Path, default=Path('dist/AudiobookQC-Mac.dmg'))
    args = parser.parse_args(argv)
    if sys.platform != 'darwin':
        raise SystemExit('This operation requires a real macOS machine / GitHub macos runner')
    if args.package:
        package_app(args.app, args.dmg)
    else:
        try:
            verify_image(args.dmg)
        except Exception as exc:
            print(f'AQC_DMG_VALIDATION_FAILED: {exc}', file=sys.stderr, flush=True)
            print('DMG diagnostic snapshot:', file=sys.stderr, flush=True)
            image = args.dmg.resolve()
            if image.is_file():
                print(f'DMG size: {image.stat().st_size} bytes', file=sys.stderr)
            for cmd in (['df', '-h', str(image.parent)],
                        ['hdiutil', 'info'], ['file', str(image)]):
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True,
                                            timeout=20, check=False)
                    print(' '.join(cmd) + ':\n' +
                          ((result.stdout or '') + (result.stderr or ''))[-4000:],
                          file=sys.stderr, flush=True)
                except Exception as diagnostic_error:
                    print(f'Diagnostic failed: {diagnostic_error}', file=sys.stderr)
            raise
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
