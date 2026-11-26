from odoo import fields, models


class PmsCheckinPartner(models.Model):
    _inherit = "pms.checkin.partner"

    origin_input_data = fields.Selection(
        [
            ("wizard", "Wizard"),
            ("wizard-precheckin", "Wizard-Precheckin"),
            ("form", "Form"),
            ("regular_customer", "Regular Customer"),
            ("ocr", "OCR"),
            ("ocr-precheckin", "OCR-Precheckin"),
            ("precheckin", "Precheckin"),
        ],
    )
    responsible_checkin_partner_id = fields.Many2one(
        comodel_name="pms.checkin.partner",
        string="Responsible adult",
        help="Guest of the same folio who is responsible for this one.",
        ondelete="set null",
    )
