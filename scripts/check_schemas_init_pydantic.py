#!/usr/bin/env python3
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCHEMAS_DIRS = [
    p / "schemas" for p in ROOT.iterdir() if p.is_dir() and (p / "schemas").is_dir()
]

errors = []

for schemas_dir in SCHEMAS_DIRS:
    init_file = schemas_dir / "__init__.py"
    if not init_file.exists():
        errors.append(f"{schemas_dir}: falta __init__.py")
        continue

    schema_files = {f.stem for f in schemas_dir.glob("*.py") if f.name != "__init__.py"}
    tree = ast.parse(init_file.read_text())
    imported = set()

    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            for name in node.names:
                imported.add(name.name)

    missing = schema_files - imported
    if missing:
        errors.append(
            f"{schemas_dir}: no importados en __init__.py → {sorted(missing)}"
        )

if errors:
    print("\n".join(errors))
    sys.exit(1)

print("✅ Schemas OK")
