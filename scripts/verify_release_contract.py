"""Fail if Windows installer model URL and GitHub Release tag can diverge.

No GitHub access needed. Both values must be identical *before* downloading
speech models, or the public setup will try to retrieve a nonmatching ZIP.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def check(root: Path = ROOT) -> str:
    installer = root/'scripts/build_installer.py'
    workflow = root/'.github/workflows/build.yml'
    src = ast.parse(installer.read_text(encoding='utf-8'))
    tags = [ast.literal_eval(node.value) for node in src.body
            if isinstance(node, ast.Assign) and any(
                isinstance(x, ast.Name) and x.id == 'RELEASE_TAG' for x in node.targets)]
    if len(tags) != 1 or not re.fullmatch(r'v\d+\.\d+\.\d+(?:-[a-zA-Z0-9.]+)?', tags[0]):
        raise ValueError('Missing or invalid RELEASE_TAG in Windows installer')
    s = workflow.read_text(encoding='utf-8')
    found = re.findall(r'^\s+tag_name:\s*([^\s#]+)\s*$', s, flags=re.M)
    if found != tags:
        raise ValueError(f'INSTALLER/RELEASE TAG MISMATCH: {tags=} {found=}')
    if ('needs: build' not in s or 'prerelease: true' not in s or
            'inputs.publish_beta == true' not in s or 'default: false' not in s):
        raise ValueError('Release workflow must remain an explicit prerelease opt-in')
    print('AQC_RELEASE_CONTRACT_OK', tags[0], flush=True)
    return tags[0]


if __name__ == '__main__':
    check()
