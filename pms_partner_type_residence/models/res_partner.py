from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    type = fields.Selection(
        selection_add=[("residence", "Residence")],
        ondelete={"residence": "set default"},
    )
    residence_partner_id = fields.Many2one(
        comodel_name="res.partner", compute="_compute_residence_partner_id"
    )

    def _compute_residence_partner_id(self):
        for partner in self:
            residence_partner = False
            for child in partner.child_ids:
                if child.type == "residence":
                    residence_partner = child
                    break
            if not residence_partner:
                residence_partner = partner
            partner.residence_partner_id = residence_partner
