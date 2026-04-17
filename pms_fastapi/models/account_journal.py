from odoo import _, models
from odoo.exceptions import UserError


class AccountJournal(models.Model):
    _inherit = "account.journal"

    def write(self, vals):
        if "type" in vals or "currency_id" in vals:
            self._check_payment_method_lines_before_recompute(vals)
        return super().write(vals)

    def _check_payment_method_lines_before_recompute(self, vals):
        """Prevent changing type or currency_id on journals whose payment
        method lines are referenced by existing payments.  The base compute
        on *inbound/outbound_payment_method_line_ids* does ``Command.clear()``
        followed by ``Command.create()``, which orphans every line that has
        payments pointing to it (FK prevents DELETE, so Odoo falls back to
        ``SET journal_id = NULL``).
        """
        for journal in self:
            changing_type = "type" in vals and vals["type"] != journal.type
            changing_currency = (
                "currency_id" in vals and vals["currency_id"] != journal.currency_id.id
            )
            if not changing_type and not changing_currency:
                continue

            lines = (
                journal.inbound_payment_method_line_ids
                | journal.outbound_payment_method_line_ids
            )
            if not lines:
                continue

            has_payments = (
                self.env["account.payment"]
                .sudo()
                .search_count(
                    [("payment_method_line_id", "in", lines.ids)],
                    limit=1,
                )
            )
            if has_payments:
                changed = []
                if changing_type:
                    changed.append(_("Type"))
                if changing_currency:
                    changed.append(_("Currency"))
                raise UserError(
                    _(
                        "You cannot change %(fields)s on journal '%(journal)s' "
                        "because it has payments linked to its payment method "
                        "lines. Changing these fields would orphan those lines "
                        "and break payment traceability.",
                        fields=" / ".join(changed),
                        journal=journal.display_name,
                    )
                )
