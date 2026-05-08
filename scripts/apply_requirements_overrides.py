#!/usr/bin/env python3
"""Replace bare package names in requirements.txt with pip-installable lines.

setuptools-odoo-get-requirements emits manifest external_dependencies verbatim,
which breaks `pip install -r` for packages not published on PyPI. This script
applies a declarative mapping (requirements-overrides.txt) over the regenerated
file so the OCA-style flow keeps working for the 99% case.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements.txt"
OVERRIDES = ROOT / "requirements-overrides.txt"


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


def main() -> int:
    if not REQUIREMENTS.is_file():
        return 0
    overrides = load_overrides()
    if not overrides:
        return 0

    original = REQUIREMENTS.read_text()
    new_lines = []
    changed = False
    for line in original.splitlines():
        key = line.strip()
        if key in overrides:
            new_lines.append(overrides[key])
            changed = True
        else:
            new_lines.append(line)

    if not changed:
        return 0

    REQUIREMENTS.write_text("\n".join(new_lines) + "\n")
    # Non-zero so pre-commit reports the file was modified, matching the
    # behaviour of formatting hooks.
    return 1


if __name__ == "__main__":
    sys.exit(main())
