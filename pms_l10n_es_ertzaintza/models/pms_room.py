# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from odoo import _, api, models
from odoo.exceptions import ValidationError


class PmsRoom(models.Model):
    _inherit = "pms.room"

    @api.constrains("institution_independent_account", "institution", "pms_property_id")
    def _check_ertzaintza_no_ses_independent_account(self):
        for record in self:
            if (
                record.institution_independent_account
                and record.institution == "ses"
                and record.pms_property_id.institution == "ertzaintza"
            ):
                raise ValidationError(
                    _(
                        "Rooms of an Ertzaintza property cannot have an "
                        "independent SES account."
                    )
                )
