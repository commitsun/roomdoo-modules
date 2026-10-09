# Copyright 2017-2018  Alexandre Díaz
# Copyright 2017  Dario Lodeiros
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class PmsReservation(models.Model):
    _inherit = "pms.reservation"

    ota_reservation_code = fields.Char(
        string="OTA Reservation Code",
        readonly=True,
    )
    children_ages = fields.Json(
        help="Age of each child of the reservation, as the channel declares "
        "them, such as [5, 13]. The price is derived from the declared number "
        "of guests, never from this, so filling it in changes nothing by "
        "itself",
    )

    def _occupancy_price_fields(self):
        """Children are charged too, so changing how many there are reprices."""
        return super()._occupancy_price_fields() + ["children"]

    # pylint: disable=W8110
    @api.depends("ota_reservation_code")
    def _compute_external_reference(self):
        super()._compute_external_reference()

    def _get_reservation_external_reference(self):
        reference = super()._get_reservation_external_reference()
        if self.ota_reservation_code:
            reference = self.ota_reservation_code
        return reference
