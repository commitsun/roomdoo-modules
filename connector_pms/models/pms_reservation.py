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

    # pylint: disable=W8110
    @api.depends("ota_reservation_code")
    def _compute_external_reference(self):
        super()._compute_external_reference()

    def _get_reservation_external_reference(self):
        reference = super()._get_reservation_external_reference()
        if self.ota_reservation_code:
            reference = self.ota_reservation_code
        return reference

    def _connector_publish_availability(self):
        """Have every channel manager publish again the nights of these
        reservations, whatever state they are in: the nights of a cancelled
        one are the nights it gave back.

        An import writes under ``connector_no_export``, and that silences the
        listeners of every connector, not only the one importing. A booking one
        channel manager brought in would never reach the others, and they would
        go on selling the room it took. So whoever imports a booking calls this
        once it is written, and each connector adds what it publishes.
        """
        return
