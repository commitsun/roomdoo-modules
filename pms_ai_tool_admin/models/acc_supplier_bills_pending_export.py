import hashlib
import json

from odoo import _, fields, models
from odoo.exceptions import UserError

from odoo.addons.pms_ai_tool.tools import guarded_tool

from .acc_export import EXPORT_FORMATS
from .pms_supplier_bills_pending import FILTER_INPUTS

SAMPLE_ROWS = 10
FILTER_NAMES = ("property_ids", "company_ids", "supplier", "min_pending")


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    def _ai_sbp_export_columns(self):
        return [
            {"key": "bill_id", "label": _("Bill id"), "kind": "integer"},
            {"key": "bill", "label": _("Bill")},
            {"key": "supplier_reference", "label": _("Supplier reference")},
            {"key": "date", "label": _("Accounting date"), "kind": "date"},
            {"key": "due_date", "label": _("Due date"), "kind": "date"},
            {"key": "company", "label": _("Company")},
            {"key": "property", "label": _("Property")},
            {"key": "journal", "label": _("Journal")},
            {"key": "supplier_id", "label": _("Supplier id"), "kind": "integer"},
            {"key": "supplier", "label": _("Supplier")},
            {"key": "supplier_vat", "label": _("Supplier VAT")},
            {"key": "payable_account", "label": _("Payable account")},
            {"key": "total", "label": _("Total"), "kind": "number", "total": True},
            {
                "key": "paid_at_cutoff",
                "label": _("Paid at cutoff"),
                "kind": "number",
                "total": True,
            },
            {
                "key": "pending_at_cutoff",
                "label": _("Pending at cutoff"),
                "kind": "number",
                "total": True,
            },
            {
                "key": "overdue_at_cutoff",
                "label": _("Overdue at cutoff"),
                "kind": "boolean",
            },
            {
                "key": "payments_at_cutoff",
                "label": _("Payments at cutoff"),
                "kind": "integer",
            },
            {
                "key": "last_payment_date",
                "label": _("Last payment date"),
                "kind": "date",
            },
        ]

    def _ai_sbp_export_stamp(self, data):
        """What the confirmation is bound to: the number of bills, the total
        pending and a hash of the bills and their pending amounts."""
        pairs = [[row["bill_id"], row["pending_at_cutoff"]] for row in data["rows"]]
        digest = hashlib.sha256(
            json.dumps(pairs, separators=(",", ":")).encode()
        ).hexdigest()
        return {
            "rows": len(pairs),
            "pending": data["totals"]["pending_at_cutoff"],
            "hash": digest,
        }

    def _ai_sbp_export_data(self, cutoff, file_format, filters):
        """The pending bills of the export, its file name and its scope."""
        domain = self._ai_sbp_domain(
            filters.get("property_ids"),
            filters.get("company_ids"),
            filters.get("supplier"),
        )
        data = self._ai_supplier_bills_pending_data(
            cutoff, domain, filters.get("min_pending")
        )
        if not data["rows"]:
            raise UserError(
                _(
                    "No vendor bill is pending payment on %s with these filters: "
                    "there is nothing to export."
                )
                % cutoff
            )
        prefix = _("supplier_bills_pending")
        return domain, data, f"{prefix}_{cutoff}.{file_format}"

    @guarded_tool(
        input_schema=dict(
            FILTER_INPUTS,
            file_format={
                "type": "string",
                "enum": EXPORT_FORMATS,
                "description": "xlsx (default): typed numbers and dates and a "
                "totals row. csv: semicolons, decimal comma and UTF-8 with BOM, "
                "as a Spanish Excel opens it.",
            },
        ),
        output_schema={
            "rows": {"type": "integer"},
            "total_pending": {"type": "number"},
            "totals": {"type": "object"},
            "complete": {"type": "boolean"},
            "file_name": {"type": "string"},
            "file_format": {"type": "string"},
            "columns": {"type": "array", "items": {"type": "string"}},
            "sample": {"type": "array", "items": {"type": "object"}},
            "drafts": {"type": "object"},
            "attachment_id": {"type": "integer"},
            "url": {"type": "string"},
        },
        write=True,
        apply="_ai_apply_acc_supplier_bills_pending_export",
        log_result=False,
    )
    def _ai_acc_supplier_bills_pending_export(
        self, cutoff=None, include_drafts=True, file_format="xlsx", **filters
    ):
        cutoff = cutoff or fields.Date.context_today(self)
        domain, data, filename = self._ai_sbp_export_data(cutoff, file_format, filters)
        stamp = self._ai_sbp_export_stamp(data)
        result = {
            "rows": stamp["rows"],
            "total_pending": stamp["pending"],
            "totals": data["totals"],
            "complete": data["complete"],
            "file_name": filename,
            "file_format": file_format,
            "columns": [column["label"] for column in self._ai_sbp_export_columns()],
            "sample": data["rows"][:SAMPLE_ROWS],
        }
        if include_drafts:
            result["drafts"] = self._ai_supplier_bill_drafts(cutoff, domain)
        return {
            "summary": _(
                "Export %(rows)s vendor bills with %(pending)s pending payment on "
                "%(cutoff)s to the file %(file)s."
            )
            % {
                "rows": stamp["rows"],
                "pending": stamp["pending"],
                "cutoff": cutoff,
                "file": filename,
            },
            "period": {"cutoff": cutoff},
            "definitions": self._ai_sbp_definitions()
            + [
                _(
                    "The file has a row per pending bill, the most pending "
                    "first, with the columns listed; sample shows its first "
                    "rows. It is saved as an attachment that only you (and the "
                    "administrators) can open: the link needs your Odoo "
                    "session. The confirmation is refused if the bills or "
                    "their pending amounts change in the meantime."
                ),
            ],
            "warnings": [] if data["complete"] else [self._ai_sbp_incomplete_warning()],
            "result": result,
            "binding": stamp,
            "plan": stamp,
        }

    def _ai_apply_acc_supplier_bills_pending_export(
        self, plan, cutoff=None, include_drafts=True, file_format="xlsx", **filters
    ):
        cutoff = cutoff or fields.Date.context_today(self)
        _domain, data, filename = self._ai_sbp_export_data(cutoff, file_format, filters)
        if self._ai_sbp_export_stamp(data) != plan:
            raise UserError(
                _("The data changed since the preview: preview the export again.")
            )
        used = [f"{name}={filters[name]}" for name in FILTER_NAMES if filters.get(name)]
        description = _(
            "Exported by the AI tool acc_supplier_bills_pending_export: vendor "
            "bills pending payment on %(cutoff)s, %(rows)s bills, %(pending)s "
            "pending. Filters: %(filters)s."
        ) % {
            "cutoff": cutoff,
            "rows": plan["rows"],
            "pending": plan["pending"],
            "filters": ", ".join(used) or _("none"),
        }
        attachment = self._ai_export_file(
            data["rows"],
            self._ai_sbp_export_columns(),
            filename,
            file_format,
            description=description,
        )
        return {
            "summary": _(
                "%(rows)s vendor bills with %(pending)s pending payment on "
                "%(cutoff)s exported to %(file)s (attachment %(id)s)."
            )
            % {
                "rows": plan["rows"],
                "pending": plan["pending"],
                "cutoff": cutoff,
                "file": attachment.name,
                "id": attachment.id,
            },
            "period": {"cutoff": cutoff},
            "warnings": [] if data["complete"] else [self._ai_sbp_incomplete_warning()],
            "result": {
                "attachment_id": attachment.id,
                "file_name": attachment.name,
                "file_format": file_format,
                "url": self._ai_export_url(attachment),
                "rows": plan["rows"],
                "total_pending": plan["pending"],
                "complete": data["complete"],
            },
        }
