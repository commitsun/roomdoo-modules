from odoo import _, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    def _check_pms_valid_invoice(self, move):
        res = super()._check_pms_valid_invoice(move)
        move._check_reservation_invoice_lock()
        return res

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
        block_domain = self.company_id._get_reservation_invoice_block_domain()
        if not block_domain:
            return True
        reservations = self.line_ids.folio_line_ids.reservation_id
        if not reservations:
            return True
        blocked = self.env["pms.reservation"].search(
            [("id", "in", reservations.ids)] + block_domain
        )
        if blocked:
            self._raise_reservation_invoice_lock(blocked)
        return True

    def _raise_reservation_invoice_lock(self, blocked):
        """Build an actionable error: the company's own message (or a generic one)
        followed by the list of reservations that prevent the validation."""
        intro = self.company_id.reservation_invoice_block_message or _(
            "You cannot validate this invoice yet: some of the invoiced reservations "
            "do not meet the conditions set to be invoiced."
        )
        label = _("Reservations preventing validation:")
        body = "\n".join(f"• {reservation.name or ''}" for reservation in blocked)
        raise UserError(f"{intro}\n\n{label}\n{body}")
