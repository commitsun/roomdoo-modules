# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import fields, models


class PmsProperty(models.Model):
    _inherit = "pms.property"

    children_max_age = fields.Integer(
        string="Children Max. Age",
        help="Age up to which a guest is charged as a child. This is a "
        "commercial decision of the property and has nothing to do with the "
        "ages the law goes by, which are read from the birthdate of the guest",
    )
