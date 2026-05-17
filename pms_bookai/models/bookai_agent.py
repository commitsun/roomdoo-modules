import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

TECHNICAL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class BookaiAgent(models.Model):
    _name = "bookai.agent"
    _inherit = ["bookai.webhook.mixin"]
    _description = "BooKAI Agent"
    _order = "name, id"

    _bookai_webhook_path = "/webhooks/agent-updated"
    _bookai_webhook_event = "agent_updated"

    # -----------------------------------------------------------------
    # Identity
    # -----------------------------------------------------------------
    name = fields.Char(required=True)
    technical_name = fields.Char(required=True)
    description = fields.Text(required=True)
    active = fields.Boolean(default=True)
    image_128 = fields.Image(max_width=128, max_height=128)

    # Computed counts (for kanban)
    tool_count = fields.Integer(compute="_compute_counts", store=False)
    allowed_agent_count = fields.Integer(compute="_compute_counts", store=False)
    kb_document_count = fields.Integer(compute="_compute_counts", store=False)

    def _compute_counts(self):
        for rec in self:
            rec.tool_count = len(rec.tool_binding_ids)
            rec.allowed_agent_count = len(rec.delegation_ids)
            rec.kb_document_count = len(rec.kb_binding_ids)

    # -----------------------------------------------------------------
    # LLM
    # -----------------------------------------------------------------
    llm_account_id = fields.Many2one(
        "bookai.llm.account",
        ondelete="restrict",
        string="LLM Account",
    )
    llm_model = fields.Char(
        string="LLM Model",
        help="Overrides the account's default model if set.",
    )
    temperature = fields.Float(default=0.3)
    max_tokens = fields.Integer(default=2048)
    sensitive_data = fields.Boolean(
        default=False,
        help="If True, forces a local provider (e.g. Ollama).",
    )

    # -----------------------------------------------------------------
    # Prompt
    # -----------------------------------------------------------------
    system_prompt = fields.Text(required=True)
    context_template = fields.Text(
        default="## Relevant information\n{kb_context}",
    )

    # -----------------------------------------------------------------
    # Knowledge Base (via binding)
    # -----------------------------------------------------------------
    kb_binding_ids = fields.One2many(
        "bookai.agent.kb.binding",
        "agent_id",
        string="KB Bindings",
    )
    kb_document_ids = fields.Many2many(
        "bookai.kb.document",
        string="KB Documents",
        compute="_compute_kb_document_ids",
        search="_search_kb_document_ids",
    )

    @api.depends("kb_binding_ids.document_id", "kb_binding_ids.active")
    def _compute_kb_document_ids(self):
        for rec in self:
            rec.kb_document_ids = rec.kb_binding_ids.filtered("active").mapped(
                "document_id"
            )

    def _search_kb_document_ids(self, operator, value):
        bindings = self.env["bookai.agent.kb.binding"].search(
            [("document_id", operator, value), ("active", "=", True)]
        )
        return [("id", "in", bindings.agent_id.ids)]

    # -----------------------------------------------------------------
    # Capa 1 — ¿Quién puede invocar al agente?
    # -----------------------------------------------------------------
    caller_type = fields.Selection(
        [
            ("internal", "Internal"),
            ("external_guest", "External Guest"),
            ("system", "System"),
            ("any", "Any"),
        ],
        required=True,
    )
    allowed_user_ids = fields.Many2many(
        "res.users",
        "bookai_agent_allowed_users_rel",
        "agent_id",
        "user_id",
        string="Allowed Users",
        help="Specific users allowed. Empty = no user restriction.",
    )
    allowed_group_ids = fields.Many2many(
        "res.groups",
        "bookai_agent_allowed_groups_rel",
        "agent_id",
        "group_id",
        string="Allowed Groups",
        help="User groups allowed. Empty = no group restriction.",
    )
    property_scope_ids = fields.Many2many(
        "pms.property",
        "bookai_agent_property_scope_rel",
        "agent_id",
        "property_id",
        string="Property Scope",
        help="For external_guest: only guests of these properties. "
        "Empty = all properties.",
    )
    delegation_ids = fields.One2many(
        "bookai.agent.delegation",
        "agent_id",
        string="Delegations",
    )
    allowed_agent_ids = fields.Many2many(
        "bookai.agent",
        string="Can Invoke Agents",
        compute="_compute_allowed_agent_ids",
        inverse="_inverse_allowed_agent_ids",
        search="_search_allowed_agent_ids",
        help="Agents this agent can delegate to. "
        "Empty = no agent-to-agent restriction.",
    )

    @api.depends("delegation_ids.delegate_agent_id", "delegation_ids.active")
    def _compute_allowed_agent_ids(self):
        for rec in self:
            rec.allowed_agent_ids = rec.delegation_ids.filtered("active").mapped(
                "delegate_agent_id"
            )

    def _inverse_allowed_agent_ids(self):
        Delegation = self.env["bookai.agent.delegation"]
        for rec in self:
            existing = rec.delegation_ids.mapped("delegate_agent_id")
            target = rec.allowed_agent_ids
            to_remove = rec.delegation_ids.filtered(
                lambda d, target=target: d.delegate_agent_id not in target
            )
            if to_remove:
                to_remove.unlink()
            for delegate in target - existing:
                Delegation.create(
                    {"agent_id": rec.id, "delegate_agent_id": delegate.id}
                )

    def _search_allowed_agent_ids(self, operator, value):
        delegations = self.env["bookai.agent.delegation"].search(
            [("delegate_agent_id", operator, value), ("active", "=", True)]
        )
        return [("id", "in", delegations.agent_id.ids)]

    # -----------------------------------------------------------------
    # Capa 2 — ¿Con qué identidad opera?
    # -----------------------------------------------------------------
    identity_mode = fields.Selection(
        [
            ("technical_user", "Technical User"),
            ("caller_identity", "Caller Identity"),
            ("technical_user_scoped", "Technical User (Scoped)"),
        ],
        required=True,
        default="technical_user",
        help=(
            "technical_user: dedicated Odoo user for the agent.\n"
            "caller_identity: inherits caller permissions.\n"
            "technical_user_scoped: technical user filtered "
            "to caller scope."
        ),
    )
    technical_user_id = fields.Many2one(
        "res.users",
        string="Technical User",
        help="Odoo user for SDK operations. Required unless "
        "identity_mode is caller_identity.",
    )

    # -----------------------------------------------------------------
    # Supervisor
    # -----------------------------------------------------------------
    is_supervisor = fields.Boolean(
        default=False,
        readonly=True,
        help="System supervisor agent. Cannot be deleted "
        "or have its technical_name/caller_type changed.",
    )

    # -----------------------------------------------------------------
    # God Mode
    # -----------------------------------------------------------------
    god_mode = fields.Boolean(
        default=False,
        help="Full unrestricted access to Odoo via JSON-RPC. "
        "All writes require human confirmation and are "
        "logged in the audit log.",
    )

    # -----------------------------------------------------------------
    # Execution modes
    # -----------------------------------------------------------------
    execution_role = fields.Selection(
        [
            ("advisor", "Advisor"),
            ("assistant", "Assistant"),
            ("operator", "Operator"),
        ],
        default="assistant",
        required=True,
        help=(
            "Advisor: read-only, proposes but never executes "
            "write operations.\n"
            "Assistant: can execute actions respecting the "
            "confirmation policy.\n"
            "Operator: executes directly within existing "
            "permissions."
        ),
    )
    confirmation_policy = fields.Selection(
        [
            ("always", "Always"),
            ("sensitive", "Sensitive actions"),
            ("irreversible", "Irreversible actions only"),
            ("never", "Never"),
        ],
        default="sensitive",
        required=True,
        help=(
            "Always: confirm every action.\n"
            "Sensitive: confirm writes and tools marked "
            "sensitive.\n"
            "Irreversible: only confirm deletions and "
            "non-reversible operations.\n"
            "Never: no confirmation required."
        ),
    )
    log_level = fields.Selection(
        [
            ("basic", "Basic"),
            ("full", "Full"),
            ("debug", "Debug"),
        ],
        default="basic",
        required=True,
        help=(
            "Basic: start/end, errors, actions, result.\n"
            "Full: + delegations, confirmations, arguments, "
            "agent chain.\n"
            "Debug: + tool I/O, effective policies, escalation "
            "reasons."
        ),
    )

    # -----------------------------------------------------------------
    # Capa 3 — Tools (via binding)
    # -----------------------------------------------------------------
    tool_binding_ids = fields.One2many(
        "bookai.agent.tool.binding",
        "agent_id",
        string="Tool Bindings",
    )

    # -----------------------------------------------------------------
    # Webhook payload
    # -----------------------------------------------------------------
    def _bookai_webhook_payload(self):
        return [
            {"agent_id": rec.id, "technical_name": rec.technical_name} for rec in self
        ]

    # -----------------------------------------------------------------
    # CRUD
    # -----------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._notify_bookai_webhook("upsert")
        return records

    def write(self, vals):
        protected = {"technical_name", "caller_type", "is_supervisor"}
        if protected & set(vals.keys()):
            if self.filtered("is_supervisor"):
                raise ValidationError(
                    _("Cannot modify protected fields of " "supervisor agents.")
                )
        result = super().write(vals)
        self._notify_bookai_webhook("upsert")
        return result

    def unlink(self):
        if any(rec.is_supervisor for rec in self):
            raise ValidationError(_("Supervisor agents cannot be deleted."))
        webhook_data = self._bookai_webhook_payload()
        result = super().unlink()
        self._notify_bookai_webhook_delete(webhook_data)
        return result

    _sql_constraints = [
        (
            "technical_name_unique",
            "unique(technical_name)",
            "Technical name must be unique.",
        ),
    ]

    @api.constrains("technical_name")
    def _check_technical_name(self):
        for rec in self:
            if rec.technical_name and not TECHNICAL_NAME_RE.match(rec.technical_name):
                raise ValidationError(
                    _(
                        "Technical name '%(name)s' is invalid. "
                        "Only lowercase letters, numbers and "
                        "hyphens are allowed.",
                        name=rec.technical_name,
                    )
                )

    @api.constrains("identity_mode", "caller_type")
    def _check_identity_caller(self):
        for rec in self:
            if rec.identity_mode == "caller_identity" and rec.caller_type in (
                "external_guest",
                "any",
            ):
                raise ValidationError(
                    _(
                        "Identity mode 'Caller Identity' cannot "
                        "be used with caller_type '%s'. External "
                        "guests do not have an Odoo user."
                    )
                    % rec.caller_type
                )

    @api.constrains("god_mode", "identity_mode")
    def _check_god_mode(self):
        for rec in self:
            if rec.god_mode and rec.identity_mode != "technical_user":
                raise ValidationError(
                    _("God mode requires identity_mode " "'Technical User'.")
                )

    @api.onchange("sensitive_data", "llm_account_id")
    def _onchange_sensitive_data(self):
        if (
            self.sensitive_data
            and self.llm_account_id
            and self.llm_account_id.provider != "ollama"
        ):
            return {
                "warning": {
                    "title": _("Sensitive Data Warning"),
                    "message": _(
                        "This agent handles sensitive data but "
                        "the LLM account provider is not Ollama "
                        "(local). Consider using a local provider "
                        "to keep data private."
                    ),
                }
            }
