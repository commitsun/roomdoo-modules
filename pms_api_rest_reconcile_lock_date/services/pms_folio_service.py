from datetime import datetime

from odoo import _
from odoo.exceptions import UserError

from odoo.addons.base_rest import restapi
from odoo.addons.base_rest_datamodel.restapi import Datamodel
from odoo.addons.component.core import Component


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
        """The corrections of a payment carry its reference: they are not the
        payment the channel updates."""
        return super()._ota_payment_extra_domain() + [
            ("amended_payment_id", "=", False)
        ]

    def _ota_payment_amount(self, payment):
        return payment._amended_amount()

    def _update_ota_payment_amount(self, payment, amount):
        """A payment of a locked period keeps its amount: the difference is
        registered with the date of the day, linked to it."""
        if payment._reconcile_lock_bypassed() or not payment._is_reconcile_locked():
            return super()._update_ota_payment_amount(payment, amount)
        payment._amend_locked_payment(
            {"amount": amount},
            correction_vals={"folio_ids": [(6, 0, payment.folio_ids.ids)]},
        )
