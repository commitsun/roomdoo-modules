from odoo import fields, models


class BookaiWhatsappTranslation(models.Model):
    _name = "bookai.whatsapp.translation"
    _description = "BooKAI WhatsApp Template Translation"
    _order = "language, id"

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
