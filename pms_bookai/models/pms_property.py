import datetime
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

_WATCHED_FIELDS = {
    "bookai_mode",
    "external_code",
    "name",
    "tz",
    "email",
    "phone",
    "bookai_online_selling",
    "bookai_sale_channel_id",
    "bookai_escalation_user_ids",
    "bookai_escalation_timeout",
    "bookai_escalation_template_id",
    "bookai_app_url",
    "bookai_wa_phone_number_id",
    "bookai_wa_access_token",
    "bookai_wa_account_id",
    "bookai_wa_verify_token",
    "bookai_wa_display_number",
}


class PmsProperty(models.Model):
    _name = "pms.property"
    _inherit = ["pms.property", "bookai.webhook.mixin"]

    _bookai_webhook_path = "/webhooks/property-updated"
    _bookai_webhook_event = "property_updated"

    bookai_mode = fields.Selection(
        [
            ("disabled", "Disabled"),
            ("manual", "Manual"),
            ("ai", "AI"),
        ],
        string="BookAI Mode",
        default="disabled",
        required=True,
        help=(
            "Controls BookAI usage for this property.\n"
            "- Disabled: no BookAI sending.\n"
            "- Manual: BookAI can be used only when triggered manually.\n"
            "- AI: BookAI can be used automatically by rules / flows."
        ),
    )

    external_code = fields.Char(
        string="External Hotel Code",
        help="External hotel code for BookAI integration " "(e.g., 'EXT_TEST').",
    )
    bookai_online_selling = fields.Boolean(
        string="BooKAI Online Selling",
        default=False,
        help="Enable online reservation selling via BooKAI.",
    )
    bookai_sale_channel_id = fields.Many2one(
        "pms.sale.channel",
        string="BooKAI Sale Channel",
        help="Sale channel used for BooKAI reservations. "
        "Required when online selling is enabled.",
    )

    # Escalation config
    bookai_escalation_user_ids = fields.Many2many(
        "res.users",
        "bookai_property_escalation_users_rel",
        "property_id",
        "user_id",
        string="Escalation Contacts",
        help="Users responsible for attending BooKAI "
        "escalations. Must have a phone number.",
    )
    bookai_escalation_timeout = fields.Integer(
        string="Escalation Timeout (min)",
        default=30,
        help="Minutes before notifying contacts. " "0 = disabled.",
    )
    bookai_escalation_template_id = fields.Many2one(
        "pms.notification.template",
        string="Escalation Template",
        help="WhatsApp notification template for " "escalation alerts.",
    )

    # App URL (per property override)
    bookai_app_url = fields.Char(
        string="BooKAI App URL",
        help="App URL for this property. If empty, "
        "uses the global roomdoo_app_url parameter.",
    )

    # WhatsApp channel config
    bookai_wa_phone_number_id = fields.Char(
        string="WA Phone Number ID",
        help="Meta Cloud API phone_number_id.",
    )
    bookai_wa_access_token = fields.Char(
        string="WA Access Token",
        groups="base.group_system",
        help="Meta Bearer token for sending messages.",
    )
    bookai_wa_account_id = fields.Char(
        string="WA Business Account ID",
        help="WhatsApp Business Account ID (for templates).",
    )
    bookai_wa_verify_token = fields.Char(
        string="WA Verify Token",
        help="Token for Meta webhook verification.",
    )
    bookai_wa_display_number = fields.Char(
        string="WA Display Number",
        help='Visible phone number (e.g. "+34 900 123 456").',
    )

    # -----------------------------------------------------------------
    # Webhook payload
    # -----------------------------------------------------------------
    def _bookai_webhook_payload(self):
        return [
            {
                "property_id": rec.id,
                "property_data": rec.get_bookai_hotel_config_info(),
            }
            for rec in self
        ]

    # -----------------------------------------------------------------
    # CRUD
    # -----------------------------------------------------------------
    def write(self, vals):
        result = super().write(vals)
        if _WATCHED_FIELDS & set(vals):
            self._notify_bookai_webhook("upsert")
        return result

    def get_bookai_hotel_config_info(self):
        self.ensure_one()
        fallback_code = ""
        if "pms_property_code" in self._fields:
            fallback_code = self.pms_property_code or ""
        return {
            "id": self.id,
            "external_code": self.external_code or fallback_code,
            "name": self.name or "",
            "bookai_mode": self.bookai_mode or "disabled",
            "tz": self.tz or "UTC",
            "email": self.email or "",
            "phone": self.phone or "",
            "bookai_online_selling": self.bookai_online_selling,
            "bookai_sale_channel_id": (
                self.bookai_sale_channel_id.id if self.bookai_sale_channel_id else False
            ),
            "bookai_escalation_timeout": (self.bookai_escalation_timeout),
            "bookai_escalation_template_code": (
                self.bookai_escalation_template_id.bookai_template_code
                if self.bookai_escalation_template_id
                else False
            ),
            "bookai_escalation_contacts": [
                {
                    "user_id": u.id,
                    "name": u.name or "",
                    "phone": u.mobile or u.phone or "",
                }
                for u in self.bookai_escalation_user_ids
                if u.mobile or u.phone
            ],
            "bookai_app_url": (
                self.bookai_app_url
                or self.env["ir.config_parameter"]
                .sudo()
                .get_param("roomdoo_app_url", "")
            ),
            "bookai_wa_phone_number_id": (self.bookai_wa_phone_number_id or False),
            "bookai_wa_access_token": (self.bookai_wa_access_token or False),
            "bookai_wa_account_id": (self.bookai_wa_account_id or False),
            "bookai_wa_verify_token": (self.bookai_wa_verify_token or False),
            "bookai_wa_display_number": (self.bookai_wa_display_number or False),
        }

    def get_bookai_hotel_public_info(self):
        self.ensure_one()
        fallback_code = ""
        if "pms_property_code" in self._fields:
            fallback_code = self.pms_property_code or ""
        return {
            "id": self.id,
            "external_code": self.external_code or fallback_code,
            "name": self.name or "",
            "tz": self.tz or "UTC",
            "email": self.email or "",
            "phone": self.phone or "",
        }

    @api.model
    def get_bookai_prices(
        self,
        property_id,
        pricelist_id,
        room_type_id,
        date_from,
        date_to,
        board_service_id=False,
    ):
        """Return [{date, price}, ...] for a room type + pricelist.

        Replicates pms_price_service._get_product_price logic
        including tax-included price adjustment.
        Called by the SDK via JSON-RPC.
        """
        pms_property = self.browse(property_id)
        pricelist = self.env["product.pricelist"].browse(pricelist_id)
        room_type = self.env["pms.room.type"].browse(room_type_id)
        product = room_type.product_id

        if isinstance(date_from, str):
            date_from = datetime.date.fromisoformat(date_from)
        if isinstance(date_to, str):
            date_to = datetime.date.fromisoformat(date_to)

        results = []
        current = date_from
        while current < date_to:
            price = pricelist._get_product_price(
                product,
                1,
                consumption_date=current,
                pms_property_id=pms_property.id,
            )
            price = self.env["account.tax"]._fix_tax_included_price_company(
                price,
                product.taxes_id,
                product.taxes_id,
                pms_property.company_id,
            )
            results.append(
                {
                    "date": current.isoformat(),
                    "price": round(price, 2),
                }
            )
            current += datetime.timedelta(days=1)
        return results

    @api.model
    def get_bookai_all_prices(self, property_id, room_type_id, date_from, date_to):
        """Prices for ALL BooKAI pricelists for a room type.

        Returns [{pricelist_id, pricelist_name,
        cancelation_rule_id, guest_rate_name, nights, total}]
        """
        pms_property = self.browse(property_id)
        room_type = self.env["pms.room.type"].browse(room_type_id)
        product = room_type.product_id

        if isinstance(date_from, str):
            date_from = datetime.date.fromisoformat(date_from)
        if isinstance(date_to, str):
            date_to = datetime.date.fromisoformat(date_to)

        # Find all pricelists linked to BooKAI channel
        pricelists = self.env["product.pricelist"].search(
            [
                "|",
                ("pms_property_ids", "in", [property_id]),
                ("pms_property_ids", "=", False),
                ("pms_sale_channel_ids.name", "=", "BooKAI"),
            ]
        )

        results = []
        for pricelist in pricelists:
            nights = []
            total = 0.0
            current = date_from
            while current < date_to:
                price = pricelist._get_product_price(
                    product,
                    1,
                    consumption_date=current,
                    pms_property_id=pms_property.id,
                )
                price = self.env["account.tax"]._fix_tax_included_price_company(
                    price,
                    product.taxes_id,
                    product.taxes_id,
                    pms_property.company_id,
                )
                nights.append(
                    {
                        "date": current.isoformat(),
                        "price": round(price, 2),
                    }
                )
                total += price
                current += datetime.timedelta(days=1)

            cancel_rule = pricelist.cancelation_rule_id
            results.append(
                {
                    "pricelist_id": pricelist.id,
                    "pricelist_name": pricelist.name or "",
                    "guest_rate_name": (pricelist.guest_rate_name or ""),
                    "cancelation_rule_id": (cancel_rule.id if cancel_rule else False),
                    "cancelation_policy_name": (
                        cancel_rule.guest_policy_name or cancel_rule.name or ""
                    )
                    if cancel_rule
                    else "",
                    "nights": nights,
                    "total": round(total, 2),
                }
            )
        return results
