import logging
from datetime import datetime

from markupsafe import Markup, escape

from odoo import _
from odoo.exceptions import UserError

from odoo.addons.account_reconcile_lock_date.models.account_payment import (
    LockedPaymentError,
)
from odoo.addons.base_rest import restapi
from odoo.addons.base_rest_datamodel.restapi import Datamodel
from odoo.addons.component.core import Component

_logger = logging.getLogger(__name__)


class PmsFolioService(Component):
    _inherit = "pms.folio.service"

    # Declared again: base_rest only routes the methods the final class
    # decorates, so an override without it takes the endpoint down.
    @restapi.method(
        [
            (
                [
                    "/<int:folio_id>/refund",
                ],
                "POST",
            )
        ],
        input_param=Datamodel("pms.transaction.info", is_list=False),
        auth="jwt_api_pms",
    )
    def create_folio_refund(self, folio_id, pms_account_payment_info):
        """A refund cannot be dated within a closed period: the core would
        silently move it to the first open day."""
        folio = self.env["pms.folio"].sudo().browse(folio_id)
        payments = self.env["account.payment"]
        if folio.exists() and pms_account_payment_info.date:
            refund_date = datetime.strptime(
                pms_account_payment_info.date, "%Y-%m-%d"
            ).date()
            lock_date = folio.company_id._get_reconcile_lock_date()
            if refund_date <= lock_date and not payments._reconcile_lock_bypassed():
                raise UserError(
                    _(
                        "The refund date %(date)s falls within a closed period "
                        "(locked up to %(lock)s). Use a later date.",
                        date=refund_date,
                        lock=lock_date,
                    )
                )
        return super().create_folio_refund(folio_id, pms_account_payment_info)

    def _ota_payment_extra_domain(self):
        """The corrections registered before 28-10-2026 carry the reference of
        their payment: they are not the payment the channel updates."""
        return super()._ota_payment_extra_domain() + [
            ("amended_payment_id", "=", False)
        ]

    def _ota_payment_amount(self, payment):
        return payment._amended_amount()

    def _update_ota_payment_amount(self, payment, amount):
        """A payment of an open period that settles a document of a closed one
        takes the new amount and is reconciled again with it. One that cannot
        change -- a payment of a closed period -- keeps its amount, and the
        folio says so for accounting: refusing would fail the whole update of
        the reservation, and the channel would keep sending it."""
        if payment._reconcile_lock_bypassed() or not payment._is_reconcile_locked():
            return super()._update_ota_payment_amount(payment, amount)
        try:
            with self.env.cr.savepoint():
                payment._amend_locked_payment({"amount": amount})
        except LockedPaymentError as error:
            self._warn_locked_payment_not_updated(payment, amount, error)

    def _warn_locked_payment_not_updated(self, payment, amount, error):
        """Once per folio: the channel sends the same amount again with every
        update of the reservation."""
        message = _(
            "The channel changed payment %(payment)s from %(old).2f to %(new).2f, "
            "but it was not updated: %(reason)s",
            payment=payment.name,
            old=payment._amended_amount(),
            new=amount,
            reason=str(error),
        )
        for folio in payment.folio_ids:
            if any(
                str(escape(message)) in (msg.body or "") for msg in folio.message_ids
            ):
                continue
            _logger.warning("Folio %s: %s", folio.name, message)
            folio.sudo().message_post(body=Markup("<p>%s</p>") % message)
