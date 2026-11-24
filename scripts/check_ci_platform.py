"""Refuse to create a misleading distributable for the wrong runner CPU/OS.

Only Windows x64 and Apple Silicon macOS are supported in build.yml.
Linux x64 source GUI is checked separately and is NOT a public package.
"""
from __future__ import annotations
import platform
import sys


def validate(target: str, system: str | None = None, machine: str | None = None) -> str:
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    targets = {
        'Windows-x64': ('windows', {'amd64', 'x86_64'}),
        'Mac-Apple-Silicon': ('darwin', {'arm64', 'aarch64'}),
    }
    if target not in targets:
        raise ValueError(f'No supported installable target defined: {target}')
    expected_system, expected_arch = targets[target]
    if system != expected_system or machine not in expected_arch:
        raise RuntimeError(f'{target} expects {expected_system}/{sorted(expected_arch)}, '
                           f'runner actually is {system}/{machine}; refusing mislabeled package')
    return f'AUDIOBOOK_QC_RUNNER_ARCH_OK: {target} ({system}/{machine})'


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python scripts/check_ci_platform.py Windows-x64|Mac-Apple-Silicon')
    print(validate(sys.argv[1]), flush=True)


if __name__ == '__main__':
    main()
