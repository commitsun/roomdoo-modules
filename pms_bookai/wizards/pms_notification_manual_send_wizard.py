from odoo import _, fields, models
from odoo.exceptions import UserError


class PmsNotificationManualSendWizard(models.TransientModel):
    _inherit = "pms.notification.manual.send.wizard"

    channel = fields.Selection(
        selection_add=[("bookai_whatsapp", "BookAI WhatsApp")],
        ondelete={"bookai_whatsapp": "cascade"},
    )

    def action_send(self):
        self.ensure_one()

        if self.channel != "bookai_whatsapp":
            return super().action_send()

        if (self.recipient_emails or "").strip():
            raise UserError(_("BookAI WhatsApp does not support raw email recipients."))
        if not self.recipient_partner_ids:
            raise UserError(
                _("Please select at least one recipient partner for WhatsApp.")
            )

        origin = self._get_origin_record()

        # One WhatsApp = one partner = one log (and one API request)
        for partner in self.recipient_partner_ids:
            origin._pms_notification_send_manual(
                template=self.template_id,
                channel=self.channel,
                send_immediately=self.send_immediately,
                extra_context={},
                recipient_mode="partners",
                recipient_partner_ids=[partner.id],
                recipient_emails=False,
            )

        return {"type": "ir.actions.act_window_close"}
