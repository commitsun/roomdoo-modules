from odoo import api, fields, models


class ResPartner(models.Model):
    _name = "res.partner"
    _inherit = ["mail.thread.phone", "res.partner"]

    def _phone_get_number_fields(self):
        """This method returns the fields to use to find the number to use to
        send an SMS on a record."""
        return ["mobile", "phone"]

    pms_partner_type = fields.Selection(
        [
            ("customer", "Customer"),
            ("supplier", "Supplier"),
            ("agency", "Agency"),
            ("guest", "Guest"),
        ],
        compute="_compute_pms_partner_type",
        store=True,
        index=True,
    )

    @api.depends(
        "is_agency", "pms_checkin_partner_ids", "customer_rank", "supplier_rank"
    )
    def _compute_pms_partner_type(self):
        for record in self:
            partner_type = "customer"
            if record.is_agency:
                partner_type = "agency"
            elif record.pms_checkin_partner_ids:
                partner_type = "guest"
            elif record.customer_rank > 0:
                partner_type = "customer"
            elif record.supplier_rank > 0:
                partner_type = "supplier"
            record.pms_partner_type = partner_type
