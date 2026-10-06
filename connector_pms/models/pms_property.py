# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models


class PmsProperty(models.Model):
    """Carries the generic manual-connect surface, so it is declared once here
    instead of once per channel manager."""

    _name = "pms.property"
    _inherit = ["pms.property", "channel.connect.mixin"]

    children_max_age = fields.Integer(
        string="Children Max. Age",
        help="Age up to which a guest is charged as a child. This is a "
        "commercial decision of the property and has nothing to do with the "
        "ages the law goes by, which are read from the birthdate of the guest",
    )

    def _channel_candidate_properties(self):
        """A property is its own scope. It carries no property field, so the
        generic lookup would find none and treat it as global, offering the
        backends of every hotel."""
        self.ensure_one()
        return self
