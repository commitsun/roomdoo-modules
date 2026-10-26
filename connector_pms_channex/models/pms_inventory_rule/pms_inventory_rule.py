# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models

from ..common.ari import stage_ari

#: Fields of a rule that move the inventory it declares, or where it applies.
INVENTORY_FIELDS = {
    "quota",
    "max_avail",
    "date_from",
    "date_to",
    "room_type_id",
    "pms_property_id",
    "sale_channel_id",
    "agency_id",
    "active",
}


class PmsInventoryRule(models.Model):
    _inherit = "pms.inventory.rule"

    def _channex_stage_availability(self):
        """Publish again the availability these rules cap.

        Only the rules of the property as a whole: they cap what a room type
        has for sale through any channel, which is what its availability is.
        What an agency or a channel has set aside is a cap of its own.
        """
        for rule in self:
            if rule.sale_channel_id or rule.agency_id:
                continue
            stage_ari(
                self.env,
                "availability",
                (rule.pms_property_id.id, rule.room_type_id.id),
                rule.date_from,
                rule.date_to,
            )

    def _channex_stage_channel_caps(self):
        """Publish again what agencies have set aside where these rules apply.

        Rules of every scope: one of an agency or a sale channel is the cap
        itself, and one of the property decides whether the agency's is any
        stricter.
        """
        for rule in self:
            stage_ari(
                self.env,
                "channel_caps",
                (
                    rule.pms_property_id.id,
                    rule.agency_id.id or None,
                    rule.room_type_id.id,
                ),
                rule.date_from,
                rule.date_to,
            )

    def _channex_stage(self):
        self._channex_stage_availability()
        self._channex_stage_channel_caps()

    def write(self, vals):
        if INVENTORY_FIELDS & set(vals) and not self.env.context.get(
            "connector_no_export"
        ):
            # A rule shortened, moved to other dates or another room type, or
            # moved to another scope stops capping nights the listener cannot
            # see any more: they are read here, before they go.
            self._channex_stage()
        return super().write(vals)
