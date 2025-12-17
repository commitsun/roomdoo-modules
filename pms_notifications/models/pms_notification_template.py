"""
pms_notifications/models/pms_notification_template.py

Functional notification template:
- Defines "what" is sent (per channel configuration lives here)
- Base module provides email configuration via mail_template_id
- Other modules extend this model to add other channels (e.g. BookAI fields)

A template can be used by multiple rules (each rule chooses a single channel).
"""

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class PmsNotificationTemplate(models.Model):
    _name = "pms.notification.template"
    _description = "PMS Notification Template"

    name = fields.Char(required=True)
    code = fields.Char(
        required=True,
        index=True,
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
