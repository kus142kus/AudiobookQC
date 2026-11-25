"""Verify the published release tag is taken from the Windows installer.

One authoritative version: RELEASE_TAG in scripts/build_installer.py.
GitHub Actions must resolve the tag from this exact source at release time,
not carry a second hardcoded version that can silently get out of sync.

Does not import heavy packages, require network access, or contact GitHub.
"""
from __future__ import annotations

import argparse
import ast
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
TAG_EXPRESSION = '${{ steps.release_version.outputs.tag }}'


def check(root: Path = ROOT) -> str:
    installer = root / 'scripts/build_installer.py'
    workflow = root / '.github/workflows/build.yml'
    if not installer.is_file() or not workflow.is_file():
        raise FileNotFoundError('Missing installer script or .github/workflows/build.yml; check hidden .github folder on upload')
    source = ast.parse(installer.read_text(encoding='utf-8'))
    tags = [ast.literal_eval(node.value) for node in source.body
            if isinstance(node, ast.Assign)
            and any(isinstance(x, ast.Name) and x.id == 'RELEASE_TAG' for x in node.targets)]
    if len(tags) != 1 or not isinstance(tags[0], str) or not re.fullmatch(r'v\d+\.\d+\.\d+(?:-[a-zA-Z0-9.]+)?', tags[0]):
        raise ValueError('Missing or invalid RELEASE_TAG in Windows installer')
    text = workflow.read_text(encoding='utf-8')
    found = re.findall(r'^\s+tag_name:\s*(.+?)\s*$', text, flags=re.M)
    if found != [TAG_EXPRESSION]:
        raise ValueError('RELEASE TAG CONTRACT FAILED: workflow must use the installer-derived version. '
                         f'Installer={tags[0]!r}; workflow tag_name={found!r}. '
                         'Most likely the hidden .github/workflows/build.yml was not replaced. '
                         'Upload the FULL source including hidden .github, then verify the workflow file on GitHub.')
    if len(re.findall(r'^\s+id:\s*release_version\s*$', text, flags=re.M)) != 1:
        raise ValueError('Release version output step must have exactly one id: release_version')
    if len(re.findall(r'^\s+run:\s*python scripts/verify_release_contract\.py --github-output\s*$', text, flags=re.M)) != 1:
        raise ValueError('Release version must come from verified installer source and GITHUB_OUTPUT')
    release_block = text.split('\n  release:', 1)
    if len(release_block) != 2:
        raise ValueError('Missing release job')
    release = release_block[1]
    if ('needs: build' not in release or 'prerelease: true' not in release or
            'inputs.publish_beta == true' not in release or 'default: false' not in text or
            'target_commitish: ${{ github.sha }}' not in release):
        raise ValueError('Release must be an explicit prerelease opt-in tied to the built commit')
    print('AQC_RELEASE_CONTRACT_OK', tags[0], flush=True)
    return tags[0]


def main() -> None:
    args = argparse.ArgumentParser(description='Audit release tag and optional GitHub Actions output')
    args.add_argument('--github-output', action='store_true', help='Write verified tag into GITHUB_OUTPUT for the release job')
    parsed = args.parse_args()
    tag = check()
    if parsed.github_output:
        out = os.environ.get('GITHUB_OUTPUT')
        if not out:
            raise RuntimeError('GITHUB_OUTPUT unavailable: only use --github-output in a GitHub Actions step')
        with Path(out).open('a', encoding='utf-8') as stream:
            stream.write(f'tag={tag}\n')


if __name__ == '__main__':
    main()
