"""Pure-standard-library undefined-name audit for early GitHub CI.

Runs BEFORE downloading any of the multi-gigabyte speech models. Specifically
catches branches like `wave.open()` without an `import wave` in run.py.
Does not replace type checking or an end-to-end packaged executable test.
"""
from __future__ import annotations

import builtins
import symtable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATHS = (
    [ROOT / "run.py"]
    + sorted((ROOT / "src" / "audiobook_qc").glob("*.py"))
    + sorted((ROOT / "scripts").glob("*.py"))
    + sorted((ROOT / "tests").glob("test_*.py"))
)
MODULE_DUNDERS = {"__name__", "__file__", "__package__", "__path__", "__spec__", "__loader__", "__cached__"}


def check_paths(paths) -> list[str]:
    errors: list[str] = []
    for path in paths:
        path = Path(path)
        tree = symtable.symtable(path.read_text(encoding="utf-8"), str(path), "exec")
        known = set(dir(builtins)) | MODULE_DUNDERS | {
            symbol.get_name()
            for symbol in tree.get_symbols()
            if symbol.is_imported() or symbol.is_assigned() or symbol.is_namespace()
        }

        def inspect_scope(scope):
            for child in scope.get_children():
                for symbol in child.get_symbols():
                    if (symbol.is_referenced() and symbol.is_global()
                            and symbol.get_name() not in known):
                        errors.append(
                            f"{path}: undefined global '{symbol.get_name()}' used by {child.get_name()}"
                        )
                inspect_scope(child)

        inspect_scope(tree)
    return errors


if __name__ == "__main__":
    issues = check_paths(DEFAULT_PATHS)
    if issues:
        print("\n".join(issues), flush=True)
        raise SystemExit(1)
    print(f"AUDIOBOOK_QC_UNDEFINED_NAMES_OK ({len(DEFAULT_PATHS)} source/test files)", flush=True)
