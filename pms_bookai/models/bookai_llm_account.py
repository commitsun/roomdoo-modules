import logging
from datetime import datetime

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class BookaiLlmAccount(models.Model):
    _name = "bookai.llm.account"
    _inherit = ["bookai.webhook.mixin"]
    _description = "BooKAI LLM Account"
    _order = "name, id"

    _bookai_webhook_path = "/webhooks/llm-account-updated"
    _bookai_webhook_event = "llm_account_updated"

    name = fields.Char(required=True)
    provider = fields.Selection(
        [
            ("ollama", "Ollama"),
            ("litellm", "LiteLLM"),
            ("openai", "OpenAI"),
            ("anthropic", "Anthropic"),
            ("custom", "Custom"),
        ],
        required=True,
    )
    api_key = fields.Char(string="API Key")
    api_base_url = fields.Char(string="API Base URL")
    default_model = fields.Char()
    monthly_token_limit = fields.Integer(default=0, help="0 = unlimited")
    tokens_used_month = fields.Integer(
        compute="_compute_tokens_used_month",
        string="Tokens Used (Month)",
    )
    active = fields.Boolean(default=True)
    notes = fields.Text()
    bookai_agent_ids = fields.One2many(
        "bookai.agent",
        "llm_account_id",
        string="Agents",
    )

    # -----------------------------------------------------------------
    # Webhook payload
    # -----------------------------------------------------------------
    def _bookai_webhook_payload(self):
        return [{"llm_account_id": rec.id} for rec in self]

    # -----------------------------------------------------------------
    # CRUD
    # -----------------------------------------------------------------
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

    def _compute_tokens_used_month(self):
        if not self.ids:
            for rec in self:
                rec.tokens_used_month = 0
            return
        first_of_month = fields.Date.today().replace(day=1)
        first_of_month_dt = datetime.combine(first_of_month, datetime.min.time())
        usage_data = self.env["bookai.agent.usage"].read_group(
            domain=[
                ("llm_account_id", "in", self.ids),
                (
                    "timestamp",
                    ">=",
                    fields.Datetime.to_string(first_of_month_dt),
                ),
            ],
            fields=["llm_account_id", "tokens_in:sum", "tokens_out:sum"],
            groupby=["llm_account_id"],
        )
        usage_map = {}
        for group in usage_data:
            account_id = group["llm_account_id"][0]
            usage_map[account_id] = (group["tokens_in"] or 0) + (
                group["tokens_out"] or 0
            )
        for rec in self:
            rec.tokens_used_month = usage_map.get(rec.id, 0)
