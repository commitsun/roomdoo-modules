from odoo import models


class PmsFolio(models.Model):
    _inherit = "pms.folio"

    def do_payment(
        self,
        payment_method_line,
        user,
        amount,
        folio,
        reservations=False,
        services=False,
        partner=False,
        date=False,
        pay_type=False,
        ref=False,
    ):
        res = super().do_payment(
            payment_method_line,
            user,
            amount,
            folio,
            reservations=reservations,
            services=services,
            partner=partner,
            date=date,
            pay_type=pay_type,
            ref=ref,
        )
        for move in folio.move_ids:
            move.sudo()._autoreconcile_folio_payments()
        return res
