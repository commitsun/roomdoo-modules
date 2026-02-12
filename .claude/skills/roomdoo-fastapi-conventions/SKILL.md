---
name: roomdoo-fastapi-conventions
description: "Conventions and patterns for developing FastAPI endpoints and Pydantic schemas in the Roomdoo project (roomdoo-modules). Use when creating or modifying FastAPI routers, Pydantic schemas, or extending pms_fastapi modules. Covers endpoint naming, helper patterns, data model conventions, CurrencyAmount usage, and module organization."
globs:
  - "**/roomdoo-modules/**/routers/**"
  - "**/roomdoo-modules/**/schemas/**"
  - "**/roomdoo-modules/pms_fastapi*/**"
  - "**/roomdoo-modules/roomdoo_fastapi/**"
---

# Roomdoo FastAPI & Pydantic Conventions

## Module Organization

- **`pms_fastapi`**: Base PMS endpoints. No localization.
- **`pms_fastapi_*`**: Extension modules (`auto_install: True`) adding fields/features via `extendable_pydantic`.
- **`roomdoo_fastapi`**: Roomdoo-specific customizations.

### Schema Extension Pattern

```python
class InvoiceSummary(invoice.InvoiceSummary, extends=True):
    newField: str | None = Field(None, alias="newField")

    @classmethod
    def from_account_move(cls, account_move):
        res = super().from_account_move(account_move)
        res.newField = account_move.some_odoo_field or None
        return res
```

## Endpoints (Routers)

### URL Conventions

- Plural (`/invoices`), singular only for single-item resources (`/user`)
- No trailing slash, kebab-case (`/sale-channels`)

### No Business Logic in Endpoints

Endpoints delegate ALL logic to a **helper** (Odoo `AbstractModel`), making it inheritable:

```python
@pms_api_router.get("/invoices", response_model=PagedCollection[InvoiceSummary])
async def list_invoices(env, filters, paging, orderBy):
    count, invoices = env["pms_api_invoice.invoice_router.helper"].new()._search(paging, filters, orderBy)
    return PagedCollection[InvoiceSummary](
        count=count,
        items=[InvoiceSummary.from_account_move(inv) for inv in invoices],
    )
```

### Helper Pattern

```python
class PmsApiInvoiceRouterHelper(models.AbstractModel):
    _name = "pms_api_invoice.invoice_router.helper"

    def _get_domain_adapter(self):
        return [("move_type", "in", ["out_invoice", "out_refund"])]

    @property
    def model_adapter(self) -> FilteredModelAdapter[AccountMove]:
        return FilteredModelAdapter[AccountMove](self.env, self._get_domain_adapter())

    def _search(self, paging, params, order):
        return self.model_adapter.search_with_count(
            params.to_odoo_domain(self.env), limit=paging.limit,
            offset=paging.offset, order=order, context=params.to_odoo_context(self.env),
        )
```

Helpers are inherited via `_inherit`: `_inherit = "pms_api_contact.contact_router.helper"`

## Data Models (Pydantic Schemas)

All schemas inherit from `PmsBaseModel` (`odoo.addons.pms_fastapi.schemas.base`).

### Naming

- API fields MUST be camelCase (via `alias`). Python names can be snake_case for Odoo auto-mapping via `_read_odoo_record()`.
  Example: `is_agency: bool = Field(False, alias="isAgency")`
- Enum values in camelCase: `inHouse = "inHouse"`, `notPaid = "notPaid"`

### Field Patterns

- **Relational fields** use `id + name` schema: `partnerId: ContactId | None = Field(None, alias="partnerId")`
- **List fields** default to `[]`, never `None`: `phones: list[Phone] = Field(default_factory=list)`
- **Monetary fields** use `CurrencyAmount` type. Set `data["_decimal_places"] = currency.decimal_places` in the factory method. Auto-rounded by `PmsBaseModel` (defaults to 2).

### Conversion Methods (`from_<odoo_model>`)

Factory `@classmethod` named `from_<odoo_model_name>`. Uses `_read_odoo_record()` for basic fields, manual mapping for relational/computed:

```python
class FolioSummary(PmsBaseModel):
    totalAmount: CurrencyAmount = Field(0.0, alias="totalAmount")
    currency: CurrencySummary | None = None
    partnerId: ContactId | None = Field(None, alias="partnerId")

    @classmethod
    def from_pms_folio(cls, folio):
        data = cls._read_odoo_record(folio)
        if folio.currency_id:
            data["_decimal_places"] = folio.currency_id.decimal_places
            data["currency"] = CurrencySummary.from_res_currency(folio.currency_id)
        if folio.partner_id:
            data["partnerId"] = ContactId.from_res_partner(folio.partner_id)
        return cls(**data)
```

### Search/Filter Schemas

Inherit from `BaseSearch`, implement `to_odoo_domain()` and optionally `to_odoo_context()`:

```python
class InvoiceSearch(BaseSearch):
    def to_odoo_domain(self, env) -> list:
        domain = []
        if self.name:
            domain = expression.AND([domain, [("name", "ilike", self.name)]])
        return domain
```
