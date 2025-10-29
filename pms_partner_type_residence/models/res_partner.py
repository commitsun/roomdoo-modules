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

    def init(self):
        self.env.cr.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS res_partner_residence_partner_id_uniq
                ON %s (parent_id)
            WHERE type = 'residence'
        """
            % self._table
        )

    def _compute_residence_partner_id(self):
        for partner in self:
            residence_partner = (
                partner.child_ids.filtered(lambda p: p.type == "residence") or None
            )
            if not residence_partner:
                residence_partner = partner
            partner.residence_partner_id = residence_partner
