# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import models


class PmsReservationLine(models.Model):
    _inherit = "pms.reservation.line"

    def _get_price_kwargs(self):
        """Children are charged on top of the price for the adults."""
        res = super()._get_price_kwargs()
        res["children"] = self.reservation_id.children
        return res
