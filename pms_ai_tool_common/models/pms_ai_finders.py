from datetime import timedelta

from odoo import _, api, models
from odoo.exceptions import UserError
from odoo.osv import expression
from odoo.tools import escape_psql

from odoo.addons.pms_ai_tool.tools import guarded_tool

MAX_RESULTS = 20
MAX_TEXT = 100
# Days on each side of the date given to pms_folio_find.
ARRIVAL_WINDOW_DAYS = 3

TEXT_INPUT = {
    "type": "string",
    "maxLength": MAX_TEXT,
    "description": "Free text, at most 100 characters. Case does not matter and "
    "it can be a part of the word.",
}
KIND_VALUES = ["service", "board", "room_type", "all"]


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    # Helpers shared by the finders

    @api.model
    def _ai_localized(self):
        """The same environment in the language of the user.

        An MCP request does not carry the language of the user, so the
        messages and the names of stages and tags would come in English.
        """
        lang = self.env.user.lang
        return self.with_context(lang=lang) if lang else self

    @api.model
    def _ai_search_text(self, text):
        text = " ".join((text or "").split())
        if not text:
            raise UserError(_("The search text is empty."))
        return text

    @api.model
    def _ai_text_domain(self, field_names, text):
        """Domain matching ``text`` in any of the fields, with no wildcards."""
        pattern = "%" + escape_psql(text) + "%"
        return expression.OR([[(name, "=ilike", pattern)] for name in field_names])

    @api.model
    def _ai_find(self, model_name, domain, warnings, order=None, what=None):
        """Search at most MAX_RESULTS records and note the cut in ``warnings``."""
        records = self.env[model_name].search(
            domain, order=order, limit=MAX_RESULTS + 1
        )
        if len(records) > MAX_RESULTS:
            warnings.append(
                _("Only the first %(shown)s %(what)s are shown: narrow the search.")
                % {"shown": MAX_RESULTS, "what": what or model_name}
            )
        return records[:MAX_RESULTS]

    @api.model
    def _ai_mask_name(self, name):
        """First name and the initial of the next word: ``Maria G.``."""
        words = (name or "").split()
        if len(words) < 2:
            return words[0] if words else ""
        return f"{words[0]} {words[1][0].upper()}."

    @api.model
    def _ai_holder(self, folio):
        """Who the folio is for, without exposing the guest.

        Companies are shown as they are. For people, the name written on the
        folio (the one the reservation is made in) is shortened.
        """
        partner = folio.partner_id
        if partner and partner.is_company:
            return partner.name
        name = folio.partner_name or partner.name
        if partner and name == partner.name and "firstname" in partner._fields:
            # partner_firstname: the order of the full name is a setting, the
            # two parts are not.
            first = (partner.firstname or "").split()
            last = (partner.lastname or "").split()
            if first and last:
                return f"{first[0]} {last[0][0].upper()}."
        return self._ai_mask_name(name)

    @api.model
    def _ai_selection_label(self, record, field_name):
        field = record._fields[field_name]
        return dict(field._description_selection(self.env)).get(record[field_name], "")

    # Scope

    @guarded_tool(
        input_schema={},
        output_schema={
            "properties": {"type": "array", "items": {"type": "object"}},
            "companies": {"type": "array", "items": {"type": "object"}},
        },
    )
    def _ai_pms_my_scope(self):
        self = self._ai_localized()
        properties = self.env["pms.property"].browse(self._ai_scope_property_ids())
        companies = self.env["res.company"].browse(self._ai_scope_company_ids())
        return {
            "summary": _("%(properties)s properties and %(companies)s companies.")
            % {"properties": len(properties), "companies": len(companies)},
            "definitions": [
                _(
                    "Property (glossary: propiedad): a hotel. Company (glossary: "
                    "sociedad): the legal entity that owns the property and "
                    "issues its invoices."
                ),
            ],
            "result": {
                "properties": [
                    {
                        "id": prop.id,
                        "name": prop.name,
                        "code": prop.pms_property_code or None,
                        "city": prop.city or None,
                        "company_id": prop.company_id.id,
                        "company": prop.company_id.name,
                    }
                    for prop in properties.sorted("name")
                ],
                "companies": [
                    {"id": company.id, "name": company.name}
                    for company in companies.sorted("name")
                ],
            },
        }

    @guarded_tool(
        input_schema={
            "text": dict(TEXT_INPUT, description="Name, code or city of the hotel."),
        },
        output_schema={"properties": {"type": "array", "items": {"type": "object"}}},
        required_inputs=["text"],
    )
    def _ai_pms_property_find(self, text):
        self = self._ai_localized()
        text = self._ai_search_text(text)
        warnings = []
        domain = expression.AND(
            [
                [("id", "in", self._ai_scope_property_ids())],
                self._ai_text_domain(["name", "pms_property_code", "city"], text),
            ]
        )
        properties = self._ai_find(
            "pms.property", domain, warnings, order="name", what=_("properties")
        )
        return {
            "summary": _("%s properties found.") % len(properties),
            "definitions": [
                _(
                    "Only the properties the user can access are searched. The id is "
                    "what the other tools ask for as property_id."
                ),
            ],
            "warnings": warnings,
            "result": {
                "properties": [
                    {
                        "id": prop.id,
                        "name": prop.name,
                        "code": prop.pms_property_code or None,
                        "city": prop.city or None,
                        "company_id": prop.company_id.id,
                        "company": prop.company_id.name,
                    }
                    for prop in properties
                ]
            },
        }

    # Folios

    @guarded_tool(
        input_schema={
            "text": dict(
                TEXT_INPUT,
                description="Folio code (for example 416/26/009337), the locator "
                "of the OTA or channel, or the name of the holder.",
            ),
            "property_id": {
                "type": "integer",
                "description": "Only this property (see pms_property_find).",
            },
            "arrival_date": {
                "type": "string",
                "format": "date",
                "description": "Only folios whose first arrival is up to "
                f"{ARRIVAL_WINDOW_DAYS} days before or after this date "
                "(YYYY-MM-DD).",
            },
        },
        output_schema={"folios": {"type": "array", "items": {"type": "object"}}},
        required_inputs=["text"],
        log_result=False,
    )
    def _ai_pms_folio_find(self, text, property_id=None, arrival_date=None):
        self = self._ai_localized()
        text = self._ai_search_text(text)
        property_ids = self._ai_scope_property_ids(
            [property_id] if property_id else None
        )
        domain = [
            ("pms_property_id", "in", property_ids),
        ] + self._ai_text_domain(["name", "external_reference", "partner_name"], text)
        if arrival_date:
            window = timedelta(days=ARRIVAL_WINDOW_DAYS)
            domain += [
                ("first_checkin", ">=", arrival_date - window),
                ("first_checkin", "<=", arrival_date + window),
            ]
        warnings = []
        folios = self._ai_find(
            "pms.folio",
            domain,
            warnings,
            order="date_order desc, id desc",
            what=_("folios"),
        )
        return {
            "summary": _("%s folios found.") % len(folios),
            "definitions": [
                _(
                    "Folio (glossary: folio): the booking file that groups the "
                    "reservations of a stay. Code: the folio number. Locator: its "
                    "reference in an external system, usually the OTA or channel "
                    "manager (glossary: localizador)."
                ),
                _(
                    "Holder: only the first name and the initial of the surname, "
                    "to protect the guests; companies are shown in full. Open the "
                    "folio in Odoo for the full name."
                ),
                _(
                    "Arrival and departure are the first check-in and the last "
                    "check-out of the folio."
                ),
            ],
            "warnings": warnings,
            "result": {
                "folios": [
                    {
                        "id": folio.id,
                        "code": folio.name,
                        "locator": folio.external_reference or None,
                        "property_id": folio.pms_property_id.id,
                        "property": folio.pms_property_id.name,
                        "arrival": folio.first_checkin,
                        "departure": folio.last_checkout,
                        "state": folio.state,
                        "state_label": self._ai_selection_label(folio, "state"),
                        "holder": self._ai_holder(folio),
                    }
                    for folio in folios
                ]
            },
        }

    # Partners

    @guarded_tool(
        input_schema={
            "text": dict(TEXT_INPUT, description="Name, tax id (VAT) or reference."),
        },
        output_schema={"partners": {"type": "array", "items": {"type": "object"}}},
        required_inputs=["text"],
    )
    def _ai_pms_partner_find(self, text):
        self = self._ai_localized()
        text = self._ai_search_text(text)
        domain = expression.AND(
            [
                ["|", ("is_company", "=", True), ("is_agency", "=", True)],
                [
                    "|",
                    ("pms_property_ids", "=", False),
                    ("pms_property_ids", "in", self._ai_scope_property_ids()),
                ],
                self._ai_text_domain(["name", "vat", "ref"], text),
            ]
        )
        warnings = []
        partners = self._ai_find(
            "res.partner", domain, warnings, order="name, id", what=_("partners")
        )
        return {
            "summary": _("%s companies or agencies found.") % len(partners),
            "definitions": [
                _(
                    "Only companies and agencies are searched; private individuals "
                    "are never returned, and no e-mail or phone is returned."
                ),
                _(
                    "Agency (glossary: agencia): a partner that brings folios as an "
                    "intermediary. Salesperson (glossary: comercial): the user "
                    "responsible for the partner."
                ),
            ],
            "warnings": warnings,
            "result": {
                "partners": [
                    {
                        "id": partner.id,
                        "name": partner.name,
                        "vat": partner.vat or None,
                        "is_agency": bool(partner.is_agency),
                        "salesperson_id": partner.user_id.id or None,
                        "salesperson": partner.user_id.name or None,
                    }
                    for partner in partners
                ]
            },
        }

    # Users and employees

    @api.model
    def _ai_employee_model(self):
        """The model to read the employees from, if the user can read any.

        The public one is a SQL view with the data that any internal user may
        see: the full employee is only for HR.
        """
        if "hr.employee.public" not in self.env:
            return None
        model = self.env["hr.employee.public"]
        if not model.check_access_rights("read", raise_exception=False):
            return None
        return model

    @guarded_tool(
        input_schema={
            "text": dict(
                TEXT_INPUT,
                description="Name or login of a user, or name or job of an "
                "employee (for example TAZ or hotel manager).",
            ),
            "property_id": {
                "type": "integer",
                "description": "Only employees who work in this property (see "
                "pms_property_find).",
            },
        },
        output_schema={
            "users": {"type": "array", "items": {"type": "object"}},
            "employees": {"type": "array", "items": {"type": "object"}},
        },
        required_inputs=["text"],
    )
    def _ai_pms_user_find(self, text, property_id=None):
        self = self._ai_localized()
        text = self._ai_search_text(text)
        property_ids = self._ai_scope_property_ids(
            [property_id] if property_id else None
        )
        warnings = []
        domain = expression.AND(
            [
                [("share", "=", False)],
                self._ai_text_domain(["name", "login"], text),
            ]
        )
        if property_id:
            domain = expression.AND(
                [domain, [("pms_property_ids", "in", [property_id])]]
            )
        users = self._ai_find(
            "res.users", domain, warnings, order="name, id", what=_("users")
        )
        employees = self._ai_find_employees(text, property_id, property_ids, warnings)
        return {
            "summary": _("%(users)s users and %(employees)s employees found.")
            % {"users": len(users), "employees": len(employees)},
            "definitions": [
                _(
                    "User: an internal login of Odoo. Employee: a person of the "
                    "staff, with the job and the properties where they work. "
                    "TAZ (glossary: taz) is a job."
                ),
            ],
            "warnings": warnings,
            "result": {
                "users": [
                    {"id": user.id, "name": user.name, "login": user.login}
                    for user in users
                ],
                "employees": employees,
            },
        }

    @api.model
    def _ai_find_employees(self, text, property_id, property_ids, warnings):
        model = self._ai_employee_model()
        if model is None:
            warnings.append(_("Employees are not available for this user."))
            return []
        has_properties = "property_ids" in model._fields
        # job_title and not job_id: staff outside HR cannot read the job positions.
        domain = self._ai_text_domain(["name", "job_title"], text)
        if property_id:
            if not has_properties:
                warnings.append(
                    _("Employees cannot be filtered by property: it is not recorded.")
                )
                return []
            domain = expression.AND([domain, [("property_ids", "in", [property_id])]])
        employees = self._ai_find(
            model._name, domain, warnings, order="name, id", what=_("employees")
        )
        rows = []
        for employee in employees:
            row = {
                "id": employee.id,
                "name": employee.name,
                "job": employee.job_title or None,
                "user_id": employee.user_id.id or None,
            }
            if has_properties:
                row["properties"] = [
                    {"id": prop.id, "name": prop.name}
                    for prop in employee.property_ids
                    if prop.id in property_ids
                ]
            rows.append(row)
        return rows

    # Products, board services and room types

    @guarded_tool(
        input_schema={
            "text": dict(TEXT_INPUT, description="Name or code."),
            "kind": {
                "type": "string",
                "enum": KIND_VALUES,
                "description": "What to look for: service products, board "
                "services, room types or all of them (default).",
            },
        },
        output_schema={
            "products": {"type": "array", "items": {"type": "object"}},
            "board_services": {"type": "array", "items": {"type": "object"}},
            "room_types": {"type": "array", "items": {"type": "object"}},
        },
        required_inputs=["text"],
    )
    def _ai_pms_product_find(self, text, kind="all"):
        self = self._ai_localized()
        text = self._ai_search_text(text)
        property_ids = self._ai_scope_property_ids()
        warnings = []
        products = board_services = room_types = []
        if kind in ("service", "all"):
            products = self._ai_find_products(text, property_ids, warnings)
        if kind in ("board", "all"):
            board_services = self._ai_find_by_property(
                "pms.board.service",
                ["name", "default_code"],
                text,
                property_ids,
                warnings,
                _("board services"),
            )
        if kind in ("room_type", "all"):
            room_types = self._ai_find_by_property(
                "pms.room.type",
                ["name", "default_code"],
                text,
                property_ids,
                warnings,
                _("room types"),
            )
        has_consumed_on = "consumed_on" in self.env["product.template"]._fields
        return {
            "summary": _(
                "%(products)s service products, %(board)s board services and "
                "%(room_types)s room types found."
            )
            % {
                "products": len(products),
                "board": len(board_services),
                "room_types": len(room_types),
            },
            "definitions": [
                _(
                    "Service product (glossary: servicio): an extra that can be "
                    "added to a reservation. per_day: charged for each night. "
                    "consumed_on: when it is consumed in relation to the night "
                    "(glossary: consumo_servicio)."
                ),
                _(
                    "Board service (glossary: regimen): the meals included in a "
                    "reservation. Room type (glossary: tipo_habitacion): a kind of "
                    "room that is sold, with its own price and availability."
                ),
            ],
            "warnings": warnings,
            "result": {
                "products": [
                    {
                        "id": product.id,
                        "name": product.name,
                        "code": product.default_code or None,
                        "per_day": bool(product.per_day),
                        "consumed_on": product.consumed_on if has_consumed_on else None,
                    }
                    for product in products
                ],
                "board_services": [
                    {
                        "id": board.id,
                        "name": board.name,
                        "code": board.default_code or None,
                        "properties": self._ai_property_names(
                            board.pms_property_ids, property_ids
                        ),
                    }
                    for board in board_services
                ],
                "room_types": [
                    {
                        "id": room_type.id,
                        "name": room_type.name,
                        "code": room_type.default_code or None,
                        "class": room_type.class_id.name or None,
                        "properties": self._ai_property_names(
                            room_type.pms_property_ids, property_ids
                        ),
                    }
                    for room_type in room_types
                ],
            },
        }

    @api.model
    def _ai_property_names(self, properties, property_ids):
        """Names of the properties of the user; empty means all of them."""
        return [prop.name for prop in properties if prop.id in property_ids]

    @api.model
    def _ai_find_by_property(
        self, model_name, field_names, text, property_ids, warnings, what
    ):
        domain = expression.AND(
            [
                [
                    "|",
                    ("pms_property_ids", "=", False),
                    ("pms_property_ids", "in", property_ids),
                ],
                self._ai_text_domain(field_names, text),
            ]
        )
        return self._ai_find(model_name, domain, warnings, order="name", what=what)

    @api.model
    def _ai_find_products(self, text, property_ids, warnings):
        # The room types are products too: leave them to their own search.
        room_type_products = self.env["pms.room.type"].sudo().search([]).product_id
        domain = expression.AND(
            [
                [
                    ("type", "=", "service"),
                    ("is_pms_available", "=", True),
                    ("id", "not in", room_type_products.ids),
                    "|",
                    ("pms_property_ids", "=", False),
                    ("pms_property_ids", "in", property_ids),
                ],
                self._ai_text_domain(["name", "default_code"], text),
            ]
        )
        return self._ai_find(
            "product.product", domain, warnings, order="name, id", what=_("products")
        )
