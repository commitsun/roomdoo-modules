# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import logging

from lxml import html

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = "payment.transaction"

    booking_engine_booking_id = fields.Many2one(
        string="Booking Engine Booking",
        comodel_name="pms.booking.engine.booking",
        readonly=True,
        index=True,
        ondelete="set null",
    )
    booking_engine_idempotency_key = fields.Char(readonly=True, copy=False)

    def _reconcile_after_done(self):
        """Confirm the bookings of the booking engine once they are paid."""
        res = super()._reconcile_after_done()
        for tx in self.filtered(
            lambda tx: tx.booking_engine_booking_id and tx.state == "done"
        ):
            booking = tx.booking_engine_booking_id
            try:
                with self.env.cr.savepoint():
                    booking._on_payment_done(tx)
            except Exception:
                # The payment is recorded anyway: someone has to confirm it
                _logger.exception(
                    "Booking engine: %s not confirmed after its payment",
                    booking.folio_id.name,
                )
                booking.folio_id.message_post(
                    body=_(
                        "The booking engine could not confirm this booking after "
                        "its payment %s: confirm it by hand."
                    )
                    % tx.reference
                )
        return res

    def _get_booking_engine_next_action(self):
        """What the booking engine does to pay: submit the redirection form of
        the provider, as the payment form of Odoo would."""
        self.ensure_one()
        form_html = self._get_processing_values().get("redirect_form_html")
        forms = html.fromstring(form_html).xpath("//form") if form_html else []
        if not forms:
            raise UserError(
                _("The payment provider %s has no redirection form.")
                % self.provider_id.name
            )
        form = forms[0]
        return {
            "type": "redirect",
            "method": (form.get("method") or "post").upper(),
            "url": form.get("action"),
            "fields": {
                field.get("name"): field.get("value") or ""
                for field in form.xpath(".//input[@name]")
            },
        }

    def _get_booking_engine_attempt_status(self):
        """pending, failed, or None once paid."""
        self.ensure_one()
        if self.state in ("draft", "pending", "authorized"):
            return "pending"
        if self.state in ("cancel", "error"):
            return "failed"
        return None
