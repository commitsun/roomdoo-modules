# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
"""Queue the traveller report as the guests check in.

Same rule as SES: the first guest on board opens the communication and it
becomes sendable once every guest of the reservation is on board. A guest
arriving after the report was accepted gets a supplementary communication
(see ``_get_or_create_pv``).
"""
from odoo import models

from .ertzaintza_codes import INSTITUTION_CODE

REPORTABLE_STATES = ("onboard", "done")


class PmsCheckinPartner(models.Model):
    _inherit = "pms.checkin.partner"

    def write(self, vals):
        result = super().write(vals)
        if "state" not in vals:
            return result
        communications = self.env["pms.ertzaintza.communication"]
        for record in self:
            reservation = record.reservation_id
            if (
                record.state != "onboard"
                or reservation.pms_property_id.institution != INSTITUTION_CODE
            ):
                continue
            communication = communications._get_or_create_pv(reservation)
            guests = reservation.checkin_partner_ids
            everybody_on_board = guests and all(
                guest.state in REPORTABLE_STATES for guest in guests
            )
            if (
                everybody_on_board
                and len(guests) >= reservation.adults
                and communication.state == "incomplete"
            ):
                communication.state = "to_send"
        return result
