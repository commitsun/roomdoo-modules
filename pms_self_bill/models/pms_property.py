from odoo import api, models


class PmsProperty(models.Model):
    _inherit = "pms.property"

    @api.model
    def _get_folio_default_journal(self, partner_invoice_id, room_ids=False):
        self.ensure_one()
        partner = self.env["res.partner"].browse(partner_invoice_id)
        if (
            self.company_id.partner_id.id == partner.id
            and self.company_id.self_billed_journal_id
        ):
            return self.company_id.self_billed_journal_id
        else:
            return super()._get_folio_default_journal(
                partner_invoice_id, room_ids=room_ids
            )
