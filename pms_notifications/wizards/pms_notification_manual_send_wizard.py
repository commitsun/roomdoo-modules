"""
pms_notifications/wizards/pms_notification_manual_send_wizard.py

Manual send wizard:
- Allows user to pick a template for a given origin record (res_model/res_id)
- Allows choosing a channel (base: email)
- Allows overriding recipients: partners and/or raw emails
- Calls origin._pms_notification_send_manual(...)
   which creates the log (source of truth)
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PmsNotificationManualSendWizard(models.TransientModel):
    _name = "pms.notification.manual.send.wizard"
    _description = "PMS Notifications: Manual Send Wizard"

    res_model = fields.Char(required=True)
    res_id = fields.Integer(required=True)

    template_id = fields.Many2one(
        "pms.notification.template",
        string="Notification Template",
        required=True,
        domain=("[('active','=',True), " "('target_model_name','=',res_model)]"),
    )

    channel = fields.Selection(
        selection=[
            ("email", "Email"),
        ],
        required=True,
        default="email",
    )

    send_immediately = fields.Boolean(default=True)

    recipient_partner_ids = fields.Many2many(
        "res.partner",
        "pms_notif_manual_wiz_partner_rel",
        "wiz_id",
        "partner_id",
        string="Recipient Partners",
        help="Optional override recipients (partners).",
    )

    recipient_emails = fields.Text(
        string="Recipient Emails",
        help=(
            "Optional override recipients. Multiple emails separated by commas, "
            "semicolons or new lines."
        ),
    )

    # ------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------
    def _get_origin_record(self):
        self.ensure_one()
        record = self.env[self.res_model].browse(self.res_id).exists()
        if not record:
            raise UserError(_("The origin record does not exist or was deleted."))
        return record

    def _compute_recipient_mode(self):
        """Determine how recipients will be resolved for the log."""
        self.ensure_one()
        has_partners = bool(self.recipient_partner_ids)
        has_emails = bool((self.recipient_emails or "").strip())

        if has_partners and has_emails:
            return "partners_and_emails"
        if has_partners:
            return "partners"
        if has_emails:
            return "emails"
        return "template"

    # ------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------
    def action_send(self):
        self.ensure_one()
        origin = self._get_origin_record()

        # Channel-specific validations (base: email)
        if self.channel == "email":
            if not self.template_id.mail_template_id:
                raise UserError(
                    _(
                        "This notification template "
                        "is not configured with a Mail Template."
                    )
                )

            has_emails = bool((self.recipient_emails or "").strip())
            partners_with_email = self.recipient_partner_ids.filtered(lambda p: p.email)

            # If partners selected but none have email
            # and no raw emails provided -> block
            if (
                self.recipient_partner_ids
                and not partners_with_email
                and not has_emails
            ):
                raise UserError(
                    _(
                        "Cannot send email: all selected partners have no "
                        "email address.\n"
                        "Please add an email to at least one partner or "
                        "provide Recipient Emails."
                    )
                )

        recipient_mode = self._compute_recipient_mode()

        origin._pms_notification_send_manual(
            template=self.template_id,
            channel=self.channel,
            send_immediately=self.send_immediately,
            extra_context={},
            recipient_mode=recipient_mode,
            recipient_partner_ids=self.recipient_partner_ids.ids,
            recipient_emails=self.recipient_emails,
        )

        return {"type": "ir.actions.act_window_close"}

    @api.onchange("recipient_partner_ids", "channel", "recipient_emails")
    def _onchange_recipients_warning(self):
        """Warn when some selected partners do not have email for email channel."""
        if self.channel != "email":
            return

        partners_no_email = self.recipient_partner_ids.filtered(lambda p: not p.email)
        if partners_no_email:
            return {
                "warning": {
                    "title": _("Some partners have no email"),
                    "message": _(
                        "These partners do not have an email address and "
                        "will be ignored for email sending:\n- %s"
                    )
                    % ("\n- ".join(partners_no_email.mapped("display_name"))),
                }
            }
