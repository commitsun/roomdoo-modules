from odoo import _, models
from odoo.exceptions import UserError

BILL_TYPES = ("in_invoice", "in_refund")
MAX_MATCHES = 10


def like_escape(text):
    """Escape the wildcards of a value used with =ilike."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    def _ai_vb_domain(self, property_ids=None):
        """Vendor bills and credit notes within the user's companies and properties."""
        return self._ai_bill_scope_domain(property_ids) + [
            ("move_type", "in", BILL_TYPES)
        ]

    def _ai_vb_describe(self, move):
        return {
            "bill_id": move.id,
            "number": move.name if move.name and move.name != "/" else None,
            "supplier_reference": move.ref or None,
            "move_type": move.move_type,
            "state": move.state,
            "partner": move.partner_id.display_name or None,
            "property": move.pms_property_id.name or None,
            "invoice_date": move.invoice_date,
            "date": move.date,
            "total": round(move.amount_total, 2),
        }

    def _ai_vb_find(self, text):
        """Vendor bills whose number or supplier reference is ``text``."""
        text = text.strip()
        if not text:
            return self.env["account.move"]
        return self.env["account.move"].search(
            self._ai_vb_domain()
            + ["|", ("name", "=", text), ("ref", "=ilike", like_escape(text))],
            limit=MAX_MATCHES + 1,
        )

    def _ai_vb_ambiguous(self, text, moves):
        matches = ", ".join(
            f"{move.id} ({move.partner_id.display_name or '-'}, "
            f"{move.invoice_date or '-'}, {round(move.amount_total, 2)})"
            for move in moves[:MAX_MATCHES]
        )
        return UserError(
            _(
                "Several vendor bills match %(bill)s: %(matches)s. Call again with "
                "the id of the right one."
            )
            % {"bill": text, "matches": matches}
        )

    def _ai_vb_duplicates(self, move):
        """Posted bills of the same supplier that look like ``move``.

        Same supplier reference, or same total and same invoice date.
        """
        Move = self.env["account.move"]
        if not move.commercial_partner_id:
            return Move
        base = [
            ("id", "!=", move.id),
            ("move_type", "=", move.move_type),
            ("state", "=", "posted"),
            ("company_id", "=", move.company_id.id),
            ("commercial_partner_id", "=", move.commercial_partner_id.id),
        ]
        found = Move
        if move.ref and move.ref.strip():
            found |= Move.search(
                base + [("ref", "=ilike", like_escape(move.ref.strip()))], limit=5
            )
        if move.invoice_date:
            found |= Move.search(
                base
                + [
                    ("invoice_date", "=", move.invoice_date),
                    ("amount_total", "=", move.amount_total),
                ],
                limit=5,
            )
        return found
