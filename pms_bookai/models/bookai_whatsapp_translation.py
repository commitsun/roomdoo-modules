from odoo import api, fields, models


class BookaiWhatsappTranslation(models.Model):
    _name = "bookai.whatsapp.translation"
    _inherit = ["bookai.webhook.mixin"]
    _description = "BooKAI WhatsApp Template Translation"
    _order = "language, id"

    _bookai_webhook_path = "/webhooks/translation-updated"
    _bookai_webhook_event = "translation_updated"
    _bookai_webhook_skip_fields = {
        "meta_template_id",
        "meta_status",
    }

    template_id = fields.Many2one(
        "pms.notification.template",
        required=True,
        ondelete="cascade",
        string="Template",
    )
    wa_account_id = fields.Many2one(
        "bookai.wa.account",
        string="WA Account",
        ondelete="cascade",
        help="WABA where this template is registered in Meta.",
    )
    language = fields.Char(
        required=True,
        default="es",
        help="BCP-47 language code: es, en, fr...",
    )
    active = fields.Boolean(default=True)

    # Meta integration
    meta_template_id = fields.Char(
        string="Meta Template ID",
        help="If set, BooKAI links to this existing Meta "
        "template. If empty, BooKAI creates a new one.",
    )
    meta_status = fields.Selection(
        [
            ("draft", "Draft"),
            ("pending", "Pending approval"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
            ("error", "Error"),
        ],
        string="Meta Status",
        readonly=True,
        default="draft",
    )

    _sql_constraints = [
        (
            "template_lang_account_unique",
            "unique(template_id, language, wa_account_id)",
            "Translation must be unique per template, language and WA account.",
        ),
    ]

    # ------------------------------------------------------------------
    # Webhook payload
    # ------------------------------------------------------------------
    def _bookai_webhook_payload(self):
        return [
            {
                "translation_id": rec.id,
                "template_id": rec.template_id.id,
                "language": rec.language,
                "wa_account_id": rec.wa_account_id.id or None,
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
        skip = self._bookai_webhook_skip_fields
        if not skip.issuperset(vals.keys()):
            self._notify_bookai_webhook("upsert")
        return result

    def unlink(self):
        webhook_data = self._bookai_webhook_payload()
        result = super().unlink()
        self._notify_bookai_webhook_delete(webhook_data)
        return result
