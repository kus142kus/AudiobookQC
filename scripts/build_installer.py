"""Build a one-click Windows installer; keep offline models in the installed app.

On GitHub Releases the user downloads only AQC-Setup.exe.  During setup
Inno Setup downloads AQC-Model.zip, verifies SHA-256, extracts it into the
correct app directory and runs the offline bundle self-test.  No user archive
handling, Python installation, browser window or external paid API.

The archive is a *release asset*, NOT hosted in source control.  The installer
must be published with its matching AQC-Model.zip at the exact same version.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist'
APP = DIST / 'AudiobookQC'
STAGED = DIST / 'installer-core'
MODEL_ZIP = DIST / 'AQC-Model.zip'
INSTALLER = ROOT / 'installer' / 'AudiobookQC.iss.in'
GENERATED = DIST / 'AudiobookQC.generated.iss'
MAX_ASSET = 2 * 1024**3
RELEASE_TAG = 'v0.9.1-beta.1'
REPO = 'kus142kus/AudiobookQC'
MODEL_REL = Path('_internal/models/medium')


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            h.update(block)
    return h.hexdigest()


def _link_or_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def prepare(app: Path = APP, staged: Path = STAGED, model_zip: Path = MODEL_ZIP) -> dict:
    """Remove *only* the primary model from the installer core.

    All other libraries and models remain unchanged.  Model ZIP entries use
    paths relative to the installer directory, so automatic extraction does
    not create another nesting layer or misplace PyInstaller files.
    """
    app, staged, model_zip = map(Path, (app, staged, model_zip))
    # Guard accidental/hostile output arguments. Deleting a staging folder must
    # NEVER delete the frozen app, source tree, or the user's recordings.
    app_resolved, stage_resolved, zip_resolved = (p.resolve() for p in (app, staged, model_zip))
    if (app_resolved == stage_resolved or
            app_resolved.is_relative_to(stage_resolved) or
            stage_resolved.is_relative_to(app_resolved)):
        raise ValueError('Staging directory overlaps application files; refusing destructive copy')
    if zip_resolved.is_relative_to(app_resolved) or zip_resolved.is_relative_to(stage_resolved):
        raise ValueError('Model ZIP output may not be nested in frozen app or staging')
    if staged.exists() and staged.is_symlink():
        raise ValueError('Staging directory must not be a symlink')
    if not (app / 'AudiobookQC.exe').is_file():
        raise FileNotFoundError(f'Expected frozen Windows EXE: {app / "AudiobookQC.exe"}')
    model_dir = app / MODEL_REL
    weights = model_dir / 'model.bin'
    if not weights.is_file() or weights.stat().st_size < 100_000_000:
        raise RuntimeError('Full Whisper medium weights not found; refusing incomplete installer')
    if staged.exists():
        shutil.rmtree(staged)
    staged.mkdir(parents=True)
    if any(x.is_symlink() for x in app.rglob('*')):
        raise ValueError('Windows installer bundle contains unexpected symbolic links')
    all_files = [x for x in app.rglob('*') if x.is_file()]
    files_model = [x for x in all_files if x.is_relative_to(model_dir)]
    files_main = [x for x in all_files if not x.is_relative_to(model_dir)]
    if not files_model or not files_main:
        raise RuntimeError('Empty model or application partition')
    if not any('gigaam' in x.parts for x in files_main):
        raise RuntimeError('GigaAM absent from installer core')
    if not any('yamnet' in str(x).lower() for x in files_main):
        raise RuntimeError('YAMNet absent from installer core')
    for source in files_main:
        _link_or_copy(source, staged / source.relative_to(app))
    model_zip.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(model_zip, 'w', compression=ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
        for f in sorted(files_model):
            z.write(f, f.relative_to(app).as_posix())
    with ZipFile(model_zip) as z:
        if z.testzip() is not None:
            raise RuntimeError('Model ZIP failed CRC integrity verification')
        names = set(z.namelist())
        expected = {x.relative_to(app).as_posix() for x in files_model}
        if names != expected:
            raise RuntimeError('Model ZIP archive entries do not match source files')
        if any(not x.startswith('_internal/models/medium/') for x in names):
            raise RuntimeError('Model ZIP unexpectedly contains files outside Whisper model directory')
    if model_zip.stat().st_size >= MAX_ASSET:
        raise RuntimeError('Model ZIP exceeds GitHub Releases per-asset limit')
    manifest = {
        'tag': RELEASE_TAG,
        'url': f'https://github.com/{REPO}/releases/download/{RELEASE_TAG}/AQC-Model.zip',
        'model_zip': model_zip.name,
        'model_bytes': model_zip.stat().st_size,
        # Inno Setup ExternalSize for extractarchive must be the UNCOMPRESSED sum,
        # not the ZIP download size. This makes progress/disk estimates correct.
        'model_unpacked_bytes': sum(x.stat().st_size for x in files_model),
        'model_sha256': sha256(model_zip),
        'model_bin_sha256': sha256(weights),
        'core_files': len(files_main),
        'model_files': len(files_model),
    }
    (model_zip.parent / 'installer-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    (model_zip.parent / 'AQC-Model.zip.sha256').write_text(f'{manifest["model_sha256"]}  AQC-Model.zip\n', encoding='ascii')
    return manifest


def render(manifest: dict, *, staged: Path = STAGED, filename: Path = GENERATED,
           source_override: str | None = None, test: bool = False,
           out_dir: Path = DIST) -> Path:
    text = INSTALLER.read_text(encoding='utf-8')
    file_source = str(Path(staged).resolve()).replace('/', '\\')
    if source_override:
        model_source = str(source_override).replace('/', '\\')
        model_flags = 'external extractarchive ignoreversion'
    else:
        model_source = manifest['url']
        model_flags = 'external download extractarchive ignoreversion'
    values = {
        '@@STAGED@@': file_source,
        '@@OUT@@': str(out_dir.resolve()).replace('/', '\\'),
        '@@FILENAME@@': 'AQC-CI-Test' if test else 'AQC-Setup',
        '@@VERSION@@': manifest['tag'].removeprefix('v'),
        '@@APPID@@': 'AudiobookQC-CI-Installer-Test' if test else 'AudiobookQC.kus142kus',
        '@@MODEL_SOURCE@@': model_source,
        '@@MODEL_FLAGS@@': model_flags,
        '@@MODEL_BYTES@@': str(manifest['model_unpacked_bytes']),
        '@@MODEL_SHA@@': manifest['model_sha256'],
        '@@MODEL_BIN_SHA@@': manifest['model_bin_sha256'],
        '@@TEST@@': '1' if test else '0',
    }
    for k, v in values.items():
        text = text.replace(k, v)
    if '@@' in text:
        raise RuntimeError('Unresolved Inno Setup template tokens')
    if not test and (manifest['model_sha256'] not in text or manifest['url'] not in text):
        raise RuntimeError('Production installer lacks version-pinned download and checksum')
    filename.parent.mkdir(parents=True, exist_ok=True)
    filename.write_text(text, encoding='utf-8-sig')
    return filename


def find_iscc() -> Path:
    candidates = [
        shutil.which('iscc'),
        r'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
        r'C:\Program Files\Inno Setup 7\ISCC.exe',
        r'C:\Program Files\Inno Setup 6\ISCC.exe',
    ]
    for entry in candidates:
        if entry and Path(entry).is_file():
            return Path(entry)
    raise RuntimeError('Inno Setup compiler missing on GitHub CI runner (not needed by end user)')


def compile_iss(iss: Path, *, compiler: Path | None = None) -> Path:
    compiler = compiler or find_iscc()
    subprocess.run([str(compiler), '/Q', str(iss.resolve())], check=True, cwd=ROOT)
    exe_name = 'AQC-CI-Test' if 'CI-Test' in iss.read_text(encoding='utf-8-sig') else 'AQC-Setup'
    result = DIST / (exe_name + '.exe')
    if not result.is_file():
        raise FileNotFoundError(f'Inno Setup reported success but output missing: {result}')
    if result.stat().st_size >= MAX_ASSET:
        raise RuntimeError(f'Setup exceeds GitHub per-file limit: {result.stat().st_size/1024**3:.3f} GiB')
    return result


def source_self_test() -> None:
    """A real zip round-trip using a synthetic frozen-app layout (no 2 GB models)."""
    with tempfile.TemporaryDirectory(prefix='aqc-installer-test-') as tmp:
        root = Path(tmp)
        app = root / 'AudiobookQC'
        payload = {
            'AudiobookQC.exe': b'MZ\x00test-executable',
            '_internal/models/medium/model.bin': b'whisper' * 20_000_000,
            '_internal/models/medium/config.json': b'{}',
            '_internal/models/medium/tokenizer.json': b'{}',
            '_internal/models/gigaam/pytorch_model.bin': b'gigaam',
            '_internal/models/events/yamnet.onnx': b'yamnet',
            '_internal/web/index.html': b'html',
        }
        for name, data in payload.items():
            path = app / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        # All paths are explicit. Patching this module by __name__ breaks when
        # unittest loads it via importlib.util without a sys.modules entry.
        manifest = prepare(app, root / 'core', root / 'AQC-Model.zip')
        assert (root / 'core/AudiobookQC.exe').read_bytes() == payload['AudiobookQC.exe']
        assert not (root / 'core/_internal/models/medium/model.bin').exists()
        with ZipFile(root / 'AQC-Model.zip') as z:
            assert '_internal/models/medium/model.bin' in z.namelist()
            dest = root / 'install'
            shutil.copytree(root / 'core', dest)
            z.extractall(dest)
        for name, data in payload.items():
            assert (root / 'install' / name).read_bytes() == data, name
        script = render(manifest, staged=root / 'core', filename=root / 'test.iss', out_dir=root)
        source = script.read_text(encoding='utf-8-sig')
        assert 'download extractarchive' in source
        assert manifest['model_sha256'] in source
        assert manifest['url'] in source
        assert 'AQC-Model.zip' in source
        assert 'Hash: ' in source
    print('AQC_WINDOWS_INSTALLER_SOURCE_TEST_OK', flush=True)


def integration_test_inno() -> None:
    """Compile AND run the real Inno extractor with local fixture payload.

    Windows CI only; this catches Inno version, architecture, extraction path,
    unattended installation, shortcut and uninstaller integration mistakes
    before fetching multi-gigabyte models.
    """
    if os.name != 'nt':
        raise RuntimeError('Inno Setup integration test requires Windows GitHub runner')
    with tempfile.TemporaryDirectory(prefix='aqc-inno-e2e-') as tmp:
        folder = Path(tmp)
        app = folder / 'sample-app'
        payload = {
            'AudiobookQC.exe': b'MZ'+b'test',
            '_internal/models/medium/model.bin': b'whisper' * 20_000_000,
            '_internal/models/medium/config.json': b'{}',
            '_internal/models/medium/tokenizer.json': b'{}',
            '_internal/models/gigaam/pytorch_model.bin': b'gigaam',
            '_internal/models/events/yamnet.onnx': b'yamnet',
        }
        for name, blob in payload.items():
            file = app / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(blob)
        archive = folder / 'AQC-Model.zip'
        manifest = prepare(app, folder/'core', archive)
        iss = render(manifest, staged=folder/'core', filename=folder/'test.iss',
                     source_override=str(archive), test=True, out_dir=folder)
        setup = compile_iss(iss)
        installation = folder / 'installation'
        result = subprocess.run([
            str(setup), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
            '/SP-', '/DIR=' + str(installation), '/LOG=' + str(folder/'setup.log')
        ], timeout=180, check=False)
        if result.returncode != 0:
            log = (folder/'setup.log')
            details = log.read_text(encoding='utf-8-sig', errors='replace')[-3000:] if log.exists() else ''
            raise RuntimeError(f'Inno CI installation failed: {result.returncode}\n{details}')
        for name, blob in payload.items():
            if (installation/name).read_bytes() != blob:
                raise RuntimeError(f'Inno archive extraction path corrupted: {name}')
        uninstaller = installation / 'unins000.exe'
        if not uninstaller.exists():
            raise RuntimeError('Installer did not register an uninstaller')
        subprocess.run([str(uninstaller), '/VERYSILENT', '/SUPPRESSMSGBOXES',
                        '/NORESTART'], timeout=120, check=True)
    print('AQC_INNO_INSTALL_AND_UNINSTALL_INTEGRATION_OK', flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--test-inno', action='store_true')
    parser.add_argument('--compile', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        source_self_test()
    elif args.test_inno:
        integration_test_inno()
    elif args.prepare:
        m = prepare()
        render(m)
        print('AQC_INSTALLER_PREPARED', json.dumps(m, sort_keys=True), flush=True)
    elif args.compile:
        m = json.loads((DIST / 'installer-manifest.json').read_text(encoding='utf-8'))
        iss = render(m)
        setup = compile_iss(iss)
        (DIST / 'AQC-Setup.exe.sha256').write_text(f'{sha256(setup)}  AQC-Setup.exe\n', encoding='ascii')
        print('AQC_RELEASE_INSTALLER_OK:', setup.stat().st_size, 'bytes', flush=True)
    else:
        parser.error('Specify --prepare, --compile or --self-test')


if __name__ == '__main__':
    main()
