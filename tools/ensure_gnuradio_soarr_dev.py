#!/usr/bin/env python
"""Ensure gnuradio.soarr imports the workspace sources.

On Windows/conda it is easy to end up with a shadowing install at:
    <env>/Lib/site-packages/gnuradio/soarr

That directory can take precedence over your workspace code and you'll observe
"old code" being imported.

This script:
1) Deletes <purelib>/gnuradio/soarr if present (guarded + requires --yes)
2) Writes a <purelib>/gnuradio_soarr_workspace.pth that points at <repo>/python
3) Verifies that gnuradio.soarr.cltu_deframer resolves into this workspace

Usage (inside your target env):
        python tools/ensure_gnuradio_soarr_dev.py --yes

Options:
        --yes        Actually modify the environment (delete/write files).
        --dry-run    Print what would be done without modifying anything.

Exit codes:
    0: success
    1: failed
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path


def _repo_root() -> Path:
    # tools/ensure_gnuradio_soarr_dev.py -> repo root
    return Path(__file__).resolve().parents[1]


def _purelib() -> Path:
    purelib = sysconfig.get_paths().get("purelib")
    if not purelib:
        raise RuntimeError("Could not determine purelib (site-packages) path")
    return Path(purelib)


def _workspace_python_dir(repo_root: Path) -> Path:
    return (repo_root / "python").resolve()


def _looks_like_gr_soarr_install(shadow_dir: Path) -> bool:
    """Return True if the folder appears to be this module.

    This is a guardrail to avoid deleting unexpected content.
    """
    if not shadow_dir.exists() or not shadow_dir.is_dir():
        return False

    # Covers both today's camelCase filenames and the target snake_case
    # names from claude/coding-standards.md (ADR-0001, not yet executed) —
    # keeps this guardrail working before, during, and after that rename
    # without needing a second edit later.
    expected_any = {
        "__init__.py",
        "cltuFramer.py",
        "cltu_framer.py",
        "bchEncoder.py",
        "bch_encoder.py",
        "tcPrimaryHeader.py",
        "tc_primary_header.py",
        "sdlsHeader.py",
        "sdls_header.py",
        "bchDecoder.py",
        "bch_decoder.py",
    }

    present = {p.name for p in shadow_dir.iterdir() if p.is_file()}
    if present.intersection(expected_any):
        return True

    # If a future version becomes bindings-only (no .py files), allow the case
    # where we at least see a built extension with the expected prefix.
    if any(p.name.startswith("soarr_python") for p in shadow_dir.iterdir() if p.is_file()):
        return True

    return False


def _remove_shadowing_soarr_dir(*, purelib: Path, dry_run: bool, assume_yes: bool) -> bool:
    shadow_dir = (purelib / "gnuradio" / "soarr").resolve()
    if not shadow_dir.exists():
        return False

    # Extra safety: only delete if it's actually inside purelib
    purelib_resolved = purelib.resolve()
    try:
        shadow_dir.relative_to(purelib_resolved)
    except ValueError as exc:
        raise RuntimeError(f"Refusing to delete unexpected path: {shadow_dir}") from exc

    if not _looks_like_gr_soarr_install(shadow_dir):
        raise RuntimeError(
            "Refusing to delete site-packages/gnuradio/soarr because it does not look like gr-soarr.\n"
            f"Path: {shadow_dir}\n"
            "If you are sure, delete it manually (or update the script's allowlist)."
        )

    if dry_run:
        print(f"DRY RUN: would remove shadowing directory: {shadow_dir}")
        return True

    if not assume_yes:
        raise RuntimeError(
            "Shadowing directory exists and would be deleted, but --yes was not provided.\n"
            f"Path: {shadow_dir}\n"
            "Re-run with --yes to proceed, or use --dry-run to preview."
        )

    shutil.rmtree(shadow_dir)
    return True


def _ensure_workspace_pth(*, purelib: Path, workspace_python_dir: Path, dry_run: bool, assume_yes: bool) -> bool:
    pth_path = (purelib / "gnuradio_soarr_workspace.pth").resolve()
    desired_line = str(workspace_python_dir.resolve())

    if pth_path.exists():
        try:
            existing_lines = [ln.strip() for ln in pth_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except UnicodeDecodeError:
            existing_lines = [ln.strip() for ln in pth_path.read_text(encoding="latin-1").splitlines() if ln.strip()]

        if desired_line in existing_lines:
            return False

    if dry_run:
        print(f"DRY RUN: would write {pth_path} -> {desired_line}")
        return True

    if not assume_yes:
        raise RuntimeError(
            "Workspace .pth file needs to be created/updated, but --yes was not provided.\n"
            f"Target: {pth_path}\n"
            "Re-run with --yes to proceed, or use --dry-run to preview."
        )

    pth_path.write_text(desired_line + "\n", encoding="utf-8")
    return True


def _verify_import(repo_root: Path) -> None:
    expected_root = _workspace_python_dir(repo_root)

    code = "\n".join(
        [
            "import importlib",
            "from pathlib import Path",
            f"expected_root = Path({str(expected_root)!r}).resolve()",
            "m = importlib.import_module('gnuradio.soarr.cltu_deframer')",
            "loaded_from = Path(getattr(m, '__file__', '<unknown>')).resolve()",
            "if expected_root not in loaded_from.parents:",
            "    raise SystemExit(",
            "        'gnuradio.soarr.cltu_deframer is NOT importing from workspace.\\n'",
            "        f'Loaded from: {loaded_from}\\n'",
            "        f'Expected under: {expected_root}\\n'",
            "    )",
            "print(f'OK: importing from {loaded_from}')",
        ]
    )

    subprocess.check_call([sys.executable, "-c", code])


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually modify the environment (delete/write files).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without modifying anything.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv) if argv is not None else sys.argv[1:])

    repo_root = _repo_root()
    workspace_python_dir = _workspace_python_dir(repo_root)
    if not workspace_python_dir.exists():
        print(
            "ERROR: expected workspace layout:\n"
            f"  - {workspace_python_dir}\n",
            file=sys.stderr,
        )
        return 1

    purelib = _purelib()

    try:
        removed = _remove_shadowing_soarr_dir(
            purelib=purelib,
            dry_run=bool(args.dry_run),
            assume_yes=bool(args.yes),
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if removed and not args.dry_run:
        print(f"Removed shadowing directory: {purelib / 'gnuradio' / 'soarr'}")
    elif removed and args.dry_run:
        pass
    else:
        print("No shadowing site-packages/gnuradio/soarr directory found")

    if args.dry_run:
        _ensure_workspace_pth(
            purelib=purelib,
            workspace_python_dir=workspace_python_dir,
            dry_run=True,
            assume_yes=False,
        )
        print("DRY RUN: would verify import resolution")
        return 0

    changed = _ensure_workspace_pth(
        purelib=purelib,
        workspace_python_dir=workspace_python_dir,
        dry_run=False,
        assume_yes=bool(args.yes),
    )
    if changed:
        print(f"Wrote workspace path file: {purelib / 'gnuradio_soarr_workspace.pth'}")
    else:
        print("Workspace path file already up to date")

    print("Verifying import resolution")
    _verify_import(repo_root)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
