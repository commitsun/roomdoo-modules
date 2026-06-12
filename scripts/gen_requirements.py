#!/usr/bin/env python3
"""Generate requirements.txt from manifests, applying non-PyPI overrides.

A single idempotent step: it runs setuptools-odoo-get-requirements to emit the
manifests' external_dependencies, then rewrites the bare names that are not on
PyPI into pip-installable lines (declared in requirements-overrides.txt), and
writes the result once.

This replaces the previous two-hook setup (setuptools-odoo-get-requirements +
apply_requirements_overrides.py), which never converged: the generator rewrote
the override URLs back to bare names on every run while the override rewrote
them forward again, so both hooks always reported a modification and the commit
looped. Doing both in one pass makes the output a pure function of the manifests
and the overrides file, so a second run produces identical content and passes.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements.txt"
OVERRIDES = ROOT / "requirements-overrides.txt"
HEADER = "# generated from manifests external_dependencies"


def load_overrides() -> dict[str, str]:
    overrides: dict[str, str] = {}
    if not OVERRIDES.is_file():
        return overrides
    for raw in OVERRIDES.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, _, replacement = line.partition(" ")
        replacement = replacement.strip()
        if not replacement:
            sys.exit(f"{OVERRIDES.name}: missing replacement for {name!r}")
        overrides[name] = replacement
    return overrides


def generate() -> list[str]:
    """Bare requirements from manifests, via setuptools-odoo (on PATH in the
    pre-commit hook env)."""
    result = subprocess.run(
        ["setuptools-odoo-get-requirements", "--header", HEADER],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.splitlines()


def main() -> int:
    overrides = load_overrides()
    new_lines = [overrides.get(line.strip(), line) for line in generate()]
    new = "\n".join(new_lines) + "\n"
    old = REQUIREMENTS.read_text() if REQUIREMENTS.is_file() else ""
    if new == old:
        return 0
    REQUIREMENTS.write_text(new)
    # Non-zero so pre-commit reports the file was regenerated, matching the
    # behaviour of formatting hooks.
    return 1


if __name__ == "__main__":
    sys.exit(main())
