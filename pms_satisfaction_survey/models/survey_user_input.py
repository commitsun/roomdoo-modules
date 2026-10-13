from odoo import fields, models


class SurveyUserInput(models.Model):
    _inherit = "survey.user_input"

    folio_id = fields.Many2one(
        comodel_name="pms.folio",
        string="Folio",
        index=True,
        ondelete="set null",
    )
    pms_property_id = fields.Many2one(
        comodel_name="pms.property",
        string="Property",
        related="folio_id.pms_property_id",
        store=True,
        index=True,
    )
