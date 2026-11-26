from odoo import fields, models


class PmsProperty(models.Model):
    _inherit = "pms.property"

    # Check-in fields that each hotel decides whether to ask. The check-in
    # localization decides which guests they apply to.
    checkin_ask_gender = fields.Boolean(
        string="Ask the gender",
        default=False,
        help="The online check-in asks the gender of the guests.",
    )
    checkin_ask_birthdate = fields.Boolean(
        string="Ask the birthdate",
        default=False,
        help="The online check-in asks the birthdate of the guests.",
    )
    checkin_ask_document = fields.Boolean(
        string="Ask the identity document",
        default=True,
        help="The online check-in asks the type, number and issuing country "
        "of the identity document of the guests.",
    )
