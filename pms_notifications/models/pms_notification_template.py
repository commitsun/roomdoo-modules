"""
pms_notifications/models/pms_notification_template.py

Functional notification template:
- Defines "what" is sent (per channel configuration lives here)
- Base module provides email configuration via mail_template_id
- Other modules extend this model to add other channels (e.g. BookAI fields)

A template can be used by multiple rules (each rule chooses a single channel).
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.osv import expression
from odoo.tools.safe_eval import safe_eval


class PmsNotificationTemplate(models.Model):
    _name = "pms.notification.template"
    _description = "PMS Notification Template"

    name = fields.Char(required=True)
    code = fields.Char(
        required=True,
        index=True,
        copy=False,
        help="Functional unique code, e.g. 'booking_confirmation_email_v1'.",
    )

    model_id = fields.Many2one(
        "ir.model",
        string="Target Model",
        required=True,
        ondelete="cascade",
        help="Model on which this notification applies (reservation, folio, etc.).",
    )

    target_model_name = fields.Char(
        related="model_id.model",
        string="Target Model Name",
        store=True,
        readonly=True,
    )

    mail_template_id = fields.Many2one(
        "mail.template",
        string="Mail Template",
        help="Mail template used for the email channel.",
        ondelete="restrict",
    )

    description = fields.Text(
        string="Description",
        help="Functional description of when and how this template is used.",
    )

    active = fields.Boolean(default=True)

    notification_rule_ids = fields.One2many(
        "pms.property.notification.rule",
        "template_id",
        string="Notification Rules",
        help="Notification rules associated with this template.",
    )
    pms_property_ids = fields.Many2many(
        "pms.property",
        string="Allowed Properties",
        help=(
            "If empty, template is available for all properties. "
            "If not empty, template is only available for selected properties."
        ),
    )
    apply_domain = fields.Char(
        string="Apply Domain",
        default="[]",
        help=(
            "Optional Odoo domain evaluated on pms.folio to decide if this template "
            "applies. Empty or [] means it always applies."
        ),
    )

    _sql_constraints = [
        ("code_unique", "unique (code)", "Template code must be unique."),
    ]

    @api.constrains("mail_template_id", "notification_rule_ids")
    def _check_mail_template_for_email_rules(self):
        """
        If any rule uses channel=email for this template,
        mail_template_id must be configured.
        """
        for template in self:
            email_rules = template.notification_rule_ids.filtered(
                lambda r: r.channel == "email"
            )
            if email_rules and not template.mail_template_id:
                raise ValidationError(
                    f"Notification template '{template.name}' has email rules "
                    f"but no mail template configured."
                )

    @api.constrains("apply_domain")
    def _check_apply_domain(self):
        for template in self:
            template._get_apply_domain()

    @api.model
    def _property_availability_domain(self, property_id):
        return [
            "|",
            ("pms_property_ids", "=", False),
            ("pms_property_ids", "in", [property_id]),
        ]

    def _is_available_for_property(self, pms_property):
        self.ensure_one()
        return not self.pms_property_ids or pms_property in self.pms_property_ids

    def _get_apply_domain(self):
        self.ensure_one()
        domain_expr = (self.apply_domain or "").strip() or "[]"
        try:
            domain = safe_eval(domain_expr, {})
        except Exception as err:
            raise ValidationError(
                _("Invalid apply domain on template '%s'.") % self.display_name
            ) from err

        if not isinstance(domain, list | tuple):
            raise ValidationError(
                _("Apply domain on template '%s' must evaluate to a list or tuple.")
                % self.display_name
            )
        try:
            expression.normalize_domain(domain)
        except Exception as err:
            raise ValidationError(
                _("Invalid apply domain structure on template '%s'.")
                % self.display_name
            ) from err
        return list(domain)

    def _is_applicable_to_folio(self, folio):
        self.ensure_one()
        if not folio:
            raise ValueError("folio is required to check template applicability")
        if folio._name != "pms.folio":
            return False
        domain = self._get_apply_domain()
        if not domain:
            return True
        return bool(
            self.env["pms.folio"].search_count([("id", "=", folio.id)] + domain)
        )
