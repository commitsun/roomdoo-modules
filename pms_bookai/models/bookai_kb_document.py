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
    description = fields.Char(
        help="Short description of the document. Agents see "
        "this to know what the document is about. Can be "
        "overridden per agent in the binding.",
    )
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

    # Property scope (empty = all properties, PMS standard)
    pms_property_ids = fields.Many2many(
        "pms.property",
        "bookai_kb_document_pms_property_rel",
        "document_id",
        "property_id",
        string="Properties",
        help="Properties this document applies to. "
        "Empty = available for all properties.",
    )

    # Relations
    kb_binding_ids = fields.One2many(
        "bookai.agent.kb.binding",
        "document_id",
        string="Agent Bindings",
    )
    agent_ids = fields.Many2many(
        "bookai.agent",
        string="Agents",
        compute="_compute_agent_ids",
        inverse="_inverse_agent_ids",
        search="_search_agent_ids",
    )

    @api.depends("kb_binding_ids.agent_id", "kb_binding_ids.active")
    def _compute_agent_ids(self):
        for rec in self:
            rec.agent_ids = rec.kb_binding_ids.filtered("active").mapped("agent_id")

    def _inverse_agent_ids(self):
        Binding = self.env["bookai.agent.kb.binding"]
        for rec in self:
            existing = rec.kb_binding_ids.mapped("agent_id")
            target = rec.agent_ids
            to_remove = rec.kb_binding_ids.filtered(
                lambda b, target=target: b.agent_id not in target
            )
            if to_remove:
                to_remove.unlink()
            for agent in target - existing:
                Binding.create({"agent_id": agent.id, "document_id": rec.id})

    def _search_agent_ids(self, operator, value):
        bindings = self.env["bookai.agent.kb.binding"].search(
            [("agent_id", operator, value), ("active", "=", True)]
        )
        return [("id", "in", bindings.document_id.ids)]

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
