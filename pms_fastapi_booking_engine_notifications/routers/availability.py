# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import models


class PmsBookingEngineAvailabilityRouterHelper(models.AbstractModel):
    _inherit = "pms_booking_engine.availability_router.helper"

    def _get_rate_plan_name(self, pricelist):
        return pricelist.guest_rate_name or super()._get_rate_plan_name(pricelist)

    def _get_room_type_name(self, room_type):
        return room_type.guest_display_name or super()._get_room_type_name(room_type)

    def _get_room_type_beds(self, room_type):
        return room_type.bed_configuration_text or super()._get_room_type_beds(
            room_type
        )
