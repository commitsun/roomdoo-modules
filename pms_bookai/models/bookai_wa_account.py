from odoo import fields, models


class BookaiWaAccount(models.Model):
    _name = "bookai.wa.account"
    _description = "BooKAI WhatsApp Business Account"
    _order = "name, id"

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
