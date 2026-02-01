from odoo import api, fields, models
from odoo.exceptions import ValidationError


class PmsPropertyNotificationRule(models.Model):
    _inherit = "pms.property.notification.rule"

    channel = fields.Selection(
        selection_add=[("bookai_whatsapp", "BookAI WhatsApp")],
        ondelete={"bookai_whatsapp": "set default"},
    )

    @api.constrains("channel", "template_id")
    def _check_bookai_channel_requires_template_code(self):
        for rule in self:
            if rule.channel != "bookai_whatsapp":
                continue
            if not rule.template_id or not getattr(
                rule.template_id, "bookai_template_code", False
            ):
                raise ValidationError(
                    "For channel 'BookAI WhatsApp', the selected notification "
                    "template must have a BookAI Template Code."
                )
