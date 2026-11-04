from collections import defaultdict

from odoo import _, api, fields, models

# Pre-reservations (draft) are not confirmed bookings and cancelled ones are
# gone: neither is a movement, a night or a meal to count.
UNCOUNTED_STATES = ("draft", "cancel")
# Reservation types with people in the rooms. Staff stays count; out of
# service blocks hold a room but have no guests.
GUEST_TYPES = ("normal", "staff")

PROPERTY_IDS_INPUT = {
    "type": "array",
    "items": {"type": "integer"},
    "maxItems": 200,
    "description": "Properties to include. Default: all the user's.",
}


class PmsAiTools(models.AbstractModel):
    """Helpers shared by the operations tools."""

    _inherit = "pms.ai.tools"

    @api.model
    def _ai_ops_today(self):
        """Today in the timezone of the user."""
        return fields.Date.context_today(self)

    @api.model
    def _ai_ops_scope(self, property_ids, warnings):
        """Properties to report on; raises if one is out of the user's reach."""
        scope = self._ai_scope_property_ids(property_ids)
        if not scope:
            warnings.append(
                _("You have no properties assigned: there is nothing to show.")
            )
        return scope

    @api.model
    def _ai_ops_property_names(self, property_ids):
        return {
            prop.id: prop.name for prop in self.env["pms.property"].browse(property_ids)
        }

    @api.model
    def _ai_ops_mask_name(self, name):
        """Guest names leave the system as first name and initial: ``Maria G.``."""
        words = (name or "").split()
        if len(words) < 2:
            return words[0] if words else ""
        return f"{words[0]} {words[-1][0].upper()}."

    @api.model
    def _ai_ops_group_property(self, group):
        return group["pms_property_id"][0]

    @api.model
    def _ai_ops_group_day(self, group):
        """Day of a group made with the ``date:day`` granularity."""
        return fields.Date.to_date(group["__range"]["date:day"]["from"])

    @api.model
    def _ai_ops_rooms_count(self, property_ids):
        """Active rooms for overnight stays of each property."""
        groups = (
            self.env["pms.room"]
            .with_context(active_test=True)
            .read_group(
                [
                    ("pms_property_id", "in", property_ids),
                    ("room_type_id.overnight_room", "=", True),
                ],
                ["pms_property_id"],
                ["pms_property_id"],
                lazy=False,
            )
        )
        return {
            self._ai_ops_group_property(group): group["__count"] for group in groups
        }

    @api.model
    def _ai_ops_room_nights(self, date_from, date_to, property_ids):
        """Room-nights of the overnight rooms, by property and night.

        Returns ``{(property_id, date): {"occupied": n, "blocked": m}}``.
        Occupied are the nights of normal and staff reservations; blocked, the
        ones of out of service blocks. Pre-reservations and cancelled
        reservations are not counted. The count follows the Alda KPIs: rooms
        that are active and used for overnight stays.
        """
        nights = defaultdict(lambda: {"occupied": 0, "blocked": 0})
        domain = [
            ("date", ">=", date_from),
            ("date", "<=", date_to),
            ("pms_property_id", "in", property_ids),
            ("state", "not in", UNCOUNTED_STATES),
            ("room_id.active", "=", True),
            ("room_id.room_type_id.overnight_room", "=", True),
        ]
        by_type = (
            ("occupied", ("reservation_id.reservation_type", "in", GUEST_TYPES)),
            ("blocked", ("reservation_id.reservation_type", "=", "out")),
        )
        for counter, type_filter in by_type:
            groups = self.env["pms.reservation.line"].read_group(
                [*domain, type_filter],
                ["pms_property_id"],
                ["pms_property_id", "date:day"],
                lazy=False,
            )
            for group in groups:
                key = (
                    self._ai_ops_group_property(group),
                    self._ai_ops_group_day(group),
                )
                nights[key][counter] = group["__count"]
        return nights
