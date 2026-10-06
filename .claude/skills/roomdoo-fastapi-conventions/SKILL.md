---
name: roomdoo-fastapi-conventions
description: "Conventions and patterns for developing FastAPI endpoints and Pydantic schemas in the Roomdoo project (roomdoo-modules). Use when creating or modifying FastAPI routers, Pydantic schemas, error responses, or search filters, or extending pms_fastapi modules. Covers endpoint naming, helper patterns, RFC 9457 problem+json errors, free-text search guards, data model conventions, CurrencyAmount usage, and module organization."
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
async def list_invoices(
    env: AuthenticatedEnv,
    filters: Annotated[InvoiceSearch, Depends()],
    paging: Annotated[Paging, Depends(paging)],
    orderBy: Annotated[str, Depends(ContactOrderDependency)],
):
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

### Error Responses (RFC 9457)

Client/business errors are returned as **RFC 9457 problem+json**, by **returning** a
`JSONResponse` (NOT by raising). Raised exceptions go through the rest-framework handler
(`convert_exception_to_status_body`), which wraps them as plain `{"detail": ...}` with
`application/json` and no machine-readable code — too generic for the front to branch on.
So for any error the front must distinguish, RETURN a problem+json:

```python
from fastapi.responses import JSONResponse

return JSONResponse(
    status_code=400,
    media_type="application/problem+json",
    content={
        "type": "/errors/record-limit-exceeded",   # relative URI, kebab-case
        "title": "Record limit exceeded",
        "status": 400,                              # mirror status_code
        "detail": f"The export requested {count} records, max is {max_records}.",
        "requestedCount": count,                    # extension members: extra context
        "maxAllowed": max_records,
    },
)
```

Rules:
- An endpoint typed `-> SomeModel` may still `return` a `JSONResponse` for the error path
  (FastAPI honours it). Keep the happy path returning the model.
- `type` is a relative URI `/errors/<kebab-case>`; `title` is short and stable per `type`;
  `status` mirrors `status_code`; `detail` is human-readable; add extension members for
  machine-usable context (ids, counts, limits, offending `field`).
- Don't leak Odoo internals (model/field/state names) in `detail`/members.
- There's no shared problem+json builder yet; most are inline. Reuse one if present for
  your error `type` (e.g. the search-text guard builder in
  `pms_fastapi/models/fastapi_endpoint.py`).

## Data Models (Pydantic Schemas)

All schemas inherit from `PmsBaseModel` (`odoo.addons.pms_fastapi.schemas.base`).

### Reglas Pydantic (capa schema, no runtime)

- Campos required: solo la anotación de tipo, **nunca** `...` (`name: str`, no
  `name: str = Field(...)`).
- **No usar `RootModel`**. Para listas, anotar el tipo de retorno
  (`-> list[ServiceProduct]`) o `Annotated[list[X], Body()]` en el endpoint.
- Preferir **anotación de tipo de retorno** para el filtrado/serialización; reservar
  `response_model=` para cuando el tipo interno difiere del público (alinea con
  "no exponer internos de Odoo"). Hoy conviven ambos estilos en el código — sin
  obligación de unificar los endpoints existentes.

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

#### Free-text minimum-length guard

Type every **free-text** (`ilike`) query param with `SearchText` (from `schemas.base`)
instead of `str | None`. A value that is non-empty but shorter than
`MIN_SEARCH_TEXT_LENGTH` (3) is rejected automatically with a problem+json
(`type="/errors/search-text-too-short"`, `field`, `minLength`) **before** the endpoint
runs — done by `PmsApiRouter`, which wraps any endpoint with a `BaseSearch` param. No
per-field `if`, no per-endpoint code, and new search endpoints are covered automatically.

```python
from .base import BaseSearch, SearchText

class ContactSearch(BaseSearch):
    def __init__(
        self,
        # default-style: Query() as the default value
        name: SearchText = Query(default=None, description="..."),
        # Annotated-style also works:
        # foo: Annotated[SearchText, Query(description="...")] = None,
        types: list[ContactType] | None = Query(default=None),  # NOT marked
    ):
        ...
```

- Mark only scalar free-text fields. Do NOT mark lists, enums, ids, dates or booleans.
- Don't use `Query(min_length=...)`: it yields a generic 422, not the structured
  problem+json the front branches on.
- Empty/whitespace means "no filter" (not an error). The attribute name must match the
  `__init__` param name (the guard reads `getattr(self, param_name)`).
