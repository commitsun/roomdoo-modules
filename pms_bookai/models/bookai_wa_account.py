from odoo import api, fields, models


class BookaiWaAccount(models.Model):
    _name = "bookai.wa.account"
    _inherit = ["bookai.webhook.mixin"]
    _description = "BooKAI WhatsApp Business Account"
    _order = "name, id"

    _bookai_webhook_path = "/webhooks/wa-account-updated"
    _bookai_webhook_event = "wa_account_updated"

    name = fields.Char(required=True)
    waba_id = fields.Char(
        string="WABA ID",
        required=True,
        help="WhatsApp Business Account ID from Meta.",
    )
    access_token = fields.Char(
        string="Access Token",
        groups="base.group_system",
        help="Meta Bearer token for the Cloud API.",
    )
    verify_token = fields.Char(
        string="Verify Token",
        help="Token used by Meta for webhook verification.",
    )
    phone_ids = fields.One2many(
        "bookai.wa.phone",
        "wa_account_id",
        string="Phone Numbers",
    )
    active = fields.Boolean(default=True)
    notes = fields.Text()

    _sql_constraints = [
        (
            "waba_id_unique",
            "unique(waba_id)",
            "A WhatsApp Business Account with this WABA ID already exists.",
        ),
    ]

    # ------------------------------------------------------------------
    # Webhook payload
    # ------------------------------------------------------------------
    def _bookai_webhook_payload(self):
        return [
            {"wa_account_id": rec.id, "waba_id": rec.waba_id, "name": rec.name}
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
