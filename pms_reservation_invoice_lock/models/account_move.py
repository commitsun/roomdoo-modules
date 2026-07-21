from datetime import date, datetime, time, timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.tools.safe_eval import safe_eval


class AccountMove(models.Model):
    _inherit = "account.move"

    def _check_pms_valid_invoice(self, move):
        res = super()._check_pms_valid_invoice(move)
        move._check_reservation_invoice_lock()
        return res

    def _get_reservation_lock_eval_context(self):
        """Helpers available when evaluating the block domain, mirroring the tokens
        the domain widget offers in its code editor (context_today, datetime, ...)."""
        return {
            "context_today": lambda *a: fields.Date.context_today(self),
            "datetime": datetime,
            "date": date,
            "time": time,
            "timedelta": timedelta,
            "relativedelta": relativedelta,
        }

    def _check_reservation_invoice_lock(self):
        self.ensure_one()
        if self.env.user.has_group(
            "pms_reservation_invoice_lock.group_bypass_reservation_invoice_lock"
        ):
            return True
        # Only regular customer invoices are subject to the lock.
        if self.move_type != "out_invoice":
            return True
        # Down payment invoices can always be validated.
        if self._is_downpayment():
            return True
        block_domain = (
            self.pms_property_id.reservation_invoice_block_domain or ""
        ).strip()
        if not block_domain or block_domain == "[]":
            return True
        reservations = self.line_ids.folio_line_ids.reservation_id
        if not reservations:
            return True
        eval_domain = safe_eval(block_domain, self._get_reservation_lock_eval_context())
        blocked = self.env["pms.reservation"].search(
            [("id", "in", reservations.ids)] + eval_domain
        )
        if blocked:
            self._raise_reservation_invoice_lock(blocked)
        return True

    def _raise_reservation_invoice_lock(self, blocked):
        """Build an actionable error: the hotel's own message (or a generic one)
        followed by the list of reservations that prevent the validation."""
        intro = self.pms_property_id.reservation_invoice_block_message or _(
            "You cannot validate this invoice yet: some of the invoiced reservations "
            "do not meet the conditions set by the hotel to be invoiced."
        )
        label = _("Reservations preventing validation:")
        body = "\n".join(f"• {reservation.name or ''}" for reservation in blocked)
        raise UserError(f"{intro}\n\n{label}\n{body}")
