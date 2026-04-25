from odoo import api, fields, models


class BookaiWaPhone(models.Model):
    _name = "bookai.wa.phone"
    _description = "BooKAI WhatsApp Phone Number"
    _order = "display_number, id"

    name = fields.Char(
        compute="_compute_name",
        store=True,
    )
    wa_account_id = fields.Many2one(
        "bookai.wa.account",
        required=True,
        ondelete="cascade",
        string="WA Account",
    )
    phone_number_id = fields.Char(
        string="Phone Number ID",
        required=True,
        help="Meta Cloud API phone_number_id.",
    )
    display_number = fields.Char(
        string="Display Number",
        help='Visible phone number (e.g. "+34 900 123 456").',
    )
    property_ids = fields.One2many(
        "pms.property",
        "bookai_wa_phone_id",
        string="Properties",
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "phone_number_id_unique",
            "unique(phone_number_id)",
            "A phone with this phone_number_id already exists.",
        ),
    ]

    @api.depends("display_number", "phone_number_id")
    def _compute_name(self):
        for rec in self:
            phone = rec.display_number or ""
            pid = rec.phone_number_id or ""
            if phone and pid:
                rec.name = f"{pid} ({phone})"
            else:
                rec.name = pid or phone or ""
