from odoo import api, fields, models


class BookaiWaPhone(models.Model):
    _name = "bookai.wa.phone"
    _inherit = ["bookai.webhook.mixin"]
    _description = "BooKAI WhatsApp Phone Number"
    _order = "display_number, id"

    _bookai_webhook_path = "/webhooks/wa-phone-updated"
    _bookai_webhook_event = "wa_phone_updated"

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

    # ------------------------------------------------------------------
    # Webhook payload
    # ------------------------------------------------------------------
    def _bookai_webhook_payload(self):
        return [
            {
                "wa_phone_id": rec.id,
                "phone_number_id": rec.phone_number_id,
                "display_number": rec.display_number,
                "wa_account_id": rec.wa_account_id.id,
            }
            for rec in self
        ]

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._notify_bookai_webhook("upsert")
        return records

    def write(self, vals):
        result = super().write(vals)
        self._notify_bookai_webhook("upsert")
        return result

    def unlink(self):
        webhook_data = self._bookai_webhook_payload()
        result = super().unlink()
        self._notify_bookai_webhook_delete(webhook_data)
        return result
