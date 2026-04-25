import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class BookaiKbDocument(models.Model):
    _name = "bookai.kb.document"
    _inherit = ["bookai.webhook.mixin"]
    _description = "BooKAI KB Document"
    _order = "name, id"

    _bookai_webhook_path = "/webhooks/kb-updated"
    _bookai_webhook_event = "kb_updated"

    name = fields.Char(required=True)
    source_type = fields.Selection(
        [
            ("markdown", "Markdown"),
            ("pdf", "PDF"),
            ("url", "URL"),
        ],
        required=True,
    )
    doc_type = fields.Selection(
        [
            ("instruction", "Instruction"),
            ("skill", "Skill"),
            ("faq", "FAQ"),
            ("manual", "Manual"),
            ("context", "Context"),
        ],
    )
    active = fields.Boolean(default=True)

    # Content (conditional on source_type)
    content = fields.Text()
    attachment_id = fields.Many2one("ir.attachment", string="PDF Attachment")
    source_url = fields.Char(string="Source URL")

    # Runtime behaviour
    inject_always = fields.Boolean(default=True)
    vectorize = fields.Boolean(default=False)
    vector_status = fields.Selection(
        [
            ("not_needed", "Not Needed"),
            ("pending", "Pending"),
            ("ready", "Ready"),
            ("error", "Error"),
        ],
        default="not_needed",
    )

    # Relations
    agent_ids = fields.Many2many(
        "bookai.agent",
        "bookai_agent_kb_document_rel",
        "document_id",
        "agent_id",
        string="Agents",
    )

    @api.onchange("source_type")
    def _onchange_source_type(self):
        if self.source_type == "markdown":
            self.inject_always = True
            self.vectorize = False
        elif self.source_type in ("pdf", "url"):
            self.inject_always = False
            self.vectorize = True

    # -----------------------------------------------------------------
    # Webhook payload
    # -----------------------------------------------------------------
    def _bookai_webhook_payload(self):
        return [{"doc_id": rec.id, "agent_ids": rec.agent_ids.ids} for rec in self]

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
