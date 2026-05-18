---
name: oca-odoo-conventions
description: "OCA (Odoo Community Association) development conventions for Odoo 16 addons such as pms, roomdoo-modules and roomdoo-modules-private. Use when creating or modifying Odoo modules: manifests, models, fields, views/XML, security (ir.model.access.csv, groups), ORM overrides (create/write), inheritance (_inherit, selection_add, extensible hooks), tests (TransactionCase, tags) and OCA readme/setup layout. Covers the api.model_create_multi gotcha and the extensible-method pattern used in pms."
globs:
  - "**/addons/pms/**"
  - "**/addons/roomdoo-modules/**"
  - "**/addons/roomdoo-modules-private/**"
  - "**/*/models/**.py"
  - "**/*/tests/**.py"
  - "**/*/views/**.xml"
  - "**/__manifest__.py"
  - "**/security/ir.model.access.csv"
---

# OCA Odoo 16 Conventions

Conventions for OCA-style Odoo 16 addons in this workspace (`pms`,
`roomdoo-modules`). Project rules and existing module style **override**
generic advice — match the surrounding module.

## When to Use

- Creating a new Odoo module or extending an existing one (`_inherit`).
- Overriding ORM methods (`create`, `write`, computes).
- Adding fields, views, security, or tests.
- Reviewing an Odoo diff/PR for OCA compliance.

## Critical Patterns

### `create()` override — ALWAYS `@api.model_create_multi`

In Odoo 16 `BaseModel.create` is `@api.model_create_multi` (takes a **list**
of vals dicts). Many models (e.g. `pms.reservation`, `pms.room.type`) keep
that decorator. Overriding with `@api.model def create(self, vals)` silently
degrades `create` to single-dict semantics and **breaks batch creation**
(folios, imports, REST APIs).

```python
@api.model_create_multi
def create(self, vals_list):
    for vals in vals_list:
        ...  # per-record logic
    records = super().create(vals_list)   # or iterate when records differ
    return records
```

When records need individual handling, iterate and build the recordset
preserving `vals_list` order; call `super().create([vals])` per item.

### Extensible behaviour over hardcoded values

Do not hardcode selection-value checks scattered across the codebase.
Expose a method other modules can extend (the `pms` reservation_type
refactor pattern):

```python
def _get_reservation_types_with_service_pricing(self):
    return ("normal", "staff")          # base
# in a depending module:
def _get_reservation_types_with_service_pricing(self):
    return (*super()._get_reservation_types_with_service_pricing(), "long_stay")
```

### Extending a Selection / model

```python
class PmsReservation(models.Model):
    _inherit = "pms.reservation"
    reservation_type = fields.Selection(selection_add=[("long_stay", "Long Stay")])
```

`selection_add` requires the new value's behaviour to be handled; check
whether the field is **computed+stored** (e.g. `pms.reservation.reservation_type`
is computed from `folio_id`) — passing it only in reservation vals may be
overwritten by the compute; set it on the parent record too.

### Many2one / context gotchas

- A Many2one field is a recordset, not an id. Use `.id`. The `lang` context
  key needs a code string: `self.lang.code`, never the `res.lang` record.
- Avoid duplicate field labels on a model (incl. fields inherited via
  `_inherits`, e.g. `product.template` on `pms.room.type`): give distinct
  `string=`.

## Module Layout (OCA)

```
my_module/
├── __init__.py
├── __manifest__.py
├── models/{__init__.py, *.py}
├── views/*.xml
├── security/ir.model.access.csv
├── readme/{DESCRIPTION,USAGE,CONTRIBUTORS}.rst   # OCA assembles README.rst
├── static/description/index.html
└── tests/{__init__.py, test_*.py}
setup/my_module/{setup.py, odoo/addons/my_module -> ../../../../my_module}
```

`__manifest__.py` keys: `name, version (16.0.x.y.z), summary, category,
author ("…, Odoo Community Association (OCA)"), website, license (AGPL-3),
depends, data, installable`. Extension/glue modules often set
`auto_install: True`. `data` lists XML/CSV in load order (security usually
last or after the models it protects).

## Security

`security/ir.model.access.csv` header:
`id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink`
Model id is `model_<model_name_with_underscores>` (dots → underscores).
Reuse existing groups (`pms.group_pms_user`, `pms.group_pms_manager`).

## Tests

Inherit the module's common base (`odoo.addons.pms.tests.common.TestPms`,
a `TransactionCase`). Tag cross-module/post-install tests:

```python
from odoo.tests import tagged
from odoo.addons.pms.tests.common import TestPms

@tagged("-at_install", "post_install")
class TestX(TestPms):
    ...
```

Minimal reservation: folio needs `pms_property_id` + `partner_id`;
reservation needs `room_type_id` + (`checkin`/`checkout` **or**
`reservation_line_ids`) + `folio_id`.

## Commands

```bash
# Lint/format (OCA pre-commit: black, isort, flake8, pylint-odoo)
pre-commit run -a

# Syntax check edited python
python -m py_compile path/to/file.py

# Run a module's tests on a TEST database (never the dev DB)
odoo-bin -d <db_test> -u base,<dep> -i <module> \
  --test-enable --test-tags '/<module>,:TestClass' --stop-after-init
```

## Resources

- Existing FastAPI conventions: see
  [../roomdoo-fastapi-conventions/SKILL.md](../roomdoo-fastapi-conventions/SKILL.md)
- Long stay change log (worked example of these patterns):
  `/Users/miguel/odoo_16/LONG_STAY_CHANGES.md`
