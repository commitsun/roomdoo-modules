import logging
import re

from odoo import _, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class PmsNotificationLog(models.Model):
    _name = "pms.notification.log"
    _description = "PMS Notification Log"
    _order = "create_date desc, id desc"

    name = fields.Char(required=True)

    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("scheduled", "Scheduled"),
            ("sent", "Sent"),
            ("error", "Error"),
            ("cancelled", "Cancelled"),
            ("skipped", "Skipped"),
        ],
        default="pending",
        required=True,
        index=True,
    )

    property_id = fields.Many2one(
        "pms.property",
        string="Property",
        index=True,
        help="Property context (hotel) for this notification, if applicable.",
    )

    template_id = fields.Many2one(
        "pms.notification.template",
        string="Notification Template",
        required=True,
        ondelete="restrict",
        index=True,
    )

    rule_id = fields.Many2one(
        "pms.property.notification.rule",
        string="Rule",
        ondelete="set null",
        help="Rule that created this log (empty for manual sends).",
    )

    channel = fields.Selection(
        [
            ("email", "Email"),
        ],
        default="email",
        required=True,
        index=True,
        help="Delivery channel used for this log.",
    )

    scheduled_date = fields.Datetime(help="When this log is scheduled to be processed.")
    sent_date = fields.Datetime(help="When this log was successfully sent.")
    error_message = fields.Text(help="Error details if sending failed.")
    context_json = fields.Text(
        help="JSON context captured at creation time (optional).",
        default="{}",
    )

    # Optional external trace fields (useful for any future channel)
    external_reference = fields.Char(help="External provider message ID/reference.")
    external_status = fields.Char(help="External provider status.")
    external_payload = fields.Text(
        help="External provider raw response payload (JSON)."
    )

    # Origin business record being notified
    origin_model = fields.Char(required=True, index=True)
    origin_res_id = fields.Integer(required=True, index=True)

    # Unified recipients (source of truth)
    recipient_mode = fields.Selection(
        [
            ("template", "Template Default"),
            ("partners", "Partners"),
            ("emails", "Emails"),
            ("partners_and_emails", "Partners and Emails"),
        ],
        default="template",
        required=True,
        help="How recipients were chosen. Used for manual sends and auditing.",
    )

    recipient_partner_ids = fields.Many2many(
        "res.partner",
        "pms_notification_log_partner_rel",
        "log_id",
        "partner_id",
        string="Recipient Partners",
        help="Recipient partners for this notification.",
    )

    recipient_emails = fields.Text(
        string="Recipient Emails",
        help=(
            "Optional override recipients. Multiple emails separated by commas, "
            "semicolons or new lines."
        ),
    )

    # -------------------------------------------------------------------------
    # Scheduled sending batch runner
    # -------------------------------------------------------------------------
    def action_send_pending_batch(self, limit=100):
        """
        Send pending/scheduled notifications in batch.
        - Keep it small to avoid long transactions.
        - Isolate each log so one failure never stops the whole batch.
        """
        logs = self.search(
            [("state", "in", ("pending", "scheduled"))],
            order="id asc",
            limit=limit,
        )
        for log in logs:
            try:
                with self.env.cr.savepoint():
                    log.action_send_by_channel()
            except Exception as err:
                _logger.exception("Notification batch send failed for log %s", log.id)
                with self.env.cr.savepoint():
                    log.write({"state": "error", "error_message": str(err)})
        return True

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------
    def _get_record_to_notify(self):
        """Return the origin record for this log, ensuring it exists."""
        self.ensure_one()
        record = self.env[self.origin_model].browse(self.origin_res_id).exists()
        if not record:
            raise UserError(_("The origin record does not exist or was deleted."))
        return record

    def _parse_recipient_emails(self):
        """Parse recipient_emails into a clean list."""
        self.ensure_one()
        raw = (self.recipient_emails or "").strip()
        if not raw:
            return []
        parts = re.split(r"[,\n;]+", raw)
        return [p.strip() for p in parts if p and p.strip()]

    def _has_explicit_recipients(self):
        """True if this log has explicit (manual/rule) recipients configured."""
        self.ensure_one()
        return bool(self.recipient_partner_ids) or bool(
            (self.recipient_emails or "").strip()
        )

    # -------------------------------------------------------------------------
    # Sending dispatcher
    # -------------------------------------------------------------------------
    def action_send_by_channel(self):
        """Dispatch to channel-specific senders. Extended by other modules."""
        email_logs = self.filtered(lambda log: log.channel == "email")
        other_logs = self - email_logs

        if email_logs:
            email_logs.action_send_email()

        if other_logs:
            # If you see this error, you installed a channel but not its sender module.
            raise UserError(
                _("Unsupported channel(s) in this database: %s")
                % ", ".join(sorted(set(other_logs.mapped("channel"))))
            )

        return True

    # -------------------------------------------------------------------------
    # Email sending (base channel)
    # -------------------------------------------------------------------------
    def action_send_email(self):
        """
        Send email using template.mail_template_id.

        Recipient resolution:
        - If recipient_mode == "template" and no explicit recipients set on the log:
            -> use recipients configured in the mail.template (To, CC, followers, etc.)
        - Otherwise:
            -> use recipient_partner_ids (only partners that have email)
            -> plus recipient_emails raw list
            -> ignore template recipients in practice (we override email_values)
        """
        for log in self:
            try:
                template = log.template_id
                if not template or not template.mail_template_id:
                    raise ValidationError(
                        _("Email channel requires template.mail_template_id.")
                    )

                record = log._get_record_to_notify()

                use_template_recipients = (
                    log.recipient_mode == "template"
                    and not log._has_explicit_recipients()
                )

                email_values = None
                if not use_template_recipients:
                    partners_with_email = log.recipient_partner_ids.filtered(
                        lambda p: p.email
                    )
                    extra_emails = log._parse_recipient_emails()

                    if not partners_with_email and not extra_emails:
                        raise UserError(
                            _("Cannot send email: no valid recipients were provided.")
                        )

                    # Override recipients
                    email_values = {
                        # mail.mail has recipient_ids M2M;
                        # helpful for tracking and chatter
                        "recipient_ids": (
                            [(6, 0, partners_with_email.ids)]
                            if partners_with_email
                            else []
                        ),
                        # raw addresses (manual override)
                        "email_to": (
                            ", ".join(extra_emails) if extra_emails else False
                        ),
                    }

                # Send via Odoo mail.template
                template.mail_template_id.send_mail(
                    record.id,
                    force_send=True,
                    raise_exception=False,
                    email_values=email_values,
                )

                log.write(
                    {
                        "state": "sent",
                        "sent_date": fields.Datetime.now(),
                        "error_message": False,
                    }
                )

            except Exception as e:
                _logger.exception("Email notification failed for log %s", log.id)
                log.write({"state": "error", "error_message": str(e)})

        return True

    # -------------------------------------------------------------------------
    # Convenience actions
    # -------------------------------------------------------------------------
    def action_cancel(self):
        """Cancel pending/scheduled logs."""
        for log in self:
            if log.state in ("sent", "cancelled"):
                continue
            log.state = "cancelled"
        return True

    def action_retry(self):
        """Reset error logs to pending."""
        for log in self:
            if log.state != "error":
                continue
            log.write({"state": "pending", "error_message": False})
        return True
