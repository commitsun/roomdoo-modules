# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PmsPricelistOccupancy(models.Model):
    _inherit = "pms.pricelist.occupancy"

    children_fee = fields.Float(
        help="Charged for each child and night, on top of the price for the "
        "adults staying in the room",
        digits=("Product Price"),
    )

    # pylint: disable=W8110
    @api.depends("children_fee")
    def _compute_summary(self):
        return super()._compute_summary()

    def _summary_parts(self):
        """Children show up in the summary as what each of them adds."""
        parts = super()._summary_parts()
        if self.children_fee:
            parts.append(
                _("child +%s") % self._format_modifier("amount", self.children_fee)
            )
        return parts

    @api.constrains("children_fee")
    def _check_children_fee_not_negative(self):
        for record in self:
            if record.children_fee < 0:
                raise ValidationError(_("The children fee can't be negative."))
