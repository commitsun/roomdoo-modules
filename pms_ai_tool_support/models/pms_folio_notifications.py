import re
from collections import Counter
from datetime import timedelta

from odoo import _, api, fields, models

from odoo.addons.pms_ai_tool.tools import guarded_tool, truncate

from ..privacy import mask_email, mask_phone, mask_text
from .pms_support_tools import FOLIO_INPUTS

MAX_LOGS = 50
ERROR_CHARS = 300
OVERDUE_HOURS = 2

# Texts that the notification modules store in the log. They are stored in the
# language of whoever (or whatever cron) wrote them, so both are recognised. The
# two API ones come first: they go on with the text of the answer, which can say
# anything.
ERROR_KINDS = (
    ("api_refused", r"^\s*(BookAI API request failed|La solicitud de la API BookAI)"),
    (
        "api_unreachable",
        r"^\s*(BookAI API connection error|Error de conexión de la API)",
    ),
    ("not_configured", r"endpoint/token|punto final/token"),
    ("property_disabled", r"disabled for property|deshabilitado para la propiedad"),
    ("property_manual", r"MANUAL mode|modo MANUAL"),
    ("template_code", r"template code|código de plantilla"),
    (
        "no_phone",
        r"phone is missing|phone is empty|phone/country"
        r"|falta el teléfono|teléfono del destinatario.*vacío",
    ),
    (
        "bad_phone",
        r"international format|recipient country|formato internacional"
        r"|país del destinatario",
    ),
    ("origin_folio", r"Origin Folio|folio de origen"),
    ("no_recipients", r"no valid recipients|destinatarios válidos"),
)


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    @api.model
    def _support_error_kind(self, error):
        for kind, pattern in ERROR_KINDS:
            if re.search(pattern, error or "", re.IGNORECASE):
                return kind
        return None

    @api.model
    def _support_explain_error(self, log):
        """Plain words for the reason stored in the error of a failed log."""
        status = getattr(log, "bookai_last_http_status", 0) or 0
        reasons = {
            "not_configured": _(
                "BookAI is not configured in this database (API address or "
                "token missing), so nothing can be sent."
            ),
            "template_code": _(
                "The notification template has no BookAI template code, so "
                "the message cannot be built."
            ),
            "no_phone": _(
                "The guest has no phone number that the PMS can use for this "
                "message."
            ),
            "bad_phone": _(
                "The phone number of the guest is not valid or its country "
                "could not be worked out."
            ),
            "api_unreachable": _(
                "Odoo could not reach BookAI (network error or timeout); it "
                "can be retried."
            ),
            "origin_folio": _(
                "The folio of this message could not be found: check the "
                "origin folio expression of the template."
            ),
            "no_recipients": _("No recipient with an e-mail address was found."),
        }
        if self._support_error_kind(log.error_message) == "api_refused":
            if status in (401, 403):
                return _("BookAI refused the credentials of Odoo (HTTP %s).") % status
            if 400 <= status < 500:
                return (
                    _(
                        "BookAI refused the request as invalid (HTTP %s): see "
                        "the error text."
                    )
                    % status
                )
            if status >= 500:
                return (
                    _("BookAI failed on its side (HTTP %s); it can be retried.")
                    % status
                )
            return _("BookAI refused the request: see the error text.")
        return reasons.get(
            self._support_error_kind(log.error_message),
            _("Sending failed with the error shown."),
        )

    @api.model
    def _support_explain_skipped(self, log):
        kind = self._support_error_kind(log.error_message)
        if kind == "property_disabled":
            return _(
                "Skipped on purpose: BookAI is disabled for the property, so "
                "nothing was sent."
            )
        if kind == "property_manual":
            return _(
                "Skipped on purpose: the property is in manual mode, where "
                "rules do not send by themselves (a person can still send it "
                "by hand)."
            )
        return _(
            "Skipped when it was due: the folio no longer met the conditions "
            "of the rule (or no longer existed)."
        )

    @api.model
    def _support_explain_log(self, log):
        """What a state means for the person asking, in plain words."""
        state = log.state
        channel = self._support_labels("pms.notification.log", "channel").get(
            log.channel, log.channel
        )
        if state == "sent":
            return _(
                "Handed over to the %(channel)s channel on %(date)s. Odoo does "
                "not know whether the guest received or read it; for WhatsApp "
                "only BookAI knows the final delivery."
            ) % {
                "channel": channel,
                "date": self._support_local(log.sent_date) or "-",
            }
        if state == "error":
            return self._support_explain_error(log)
        if state == "skipped":
            return self._support_explain_skipped(log)
        if state == "cancelled":
            return _("Cancelled by a person before it was sent.")
        if log.scheduled_date and log.scheduled_date > fields.Datetime.now():
            return _("Waiting: it will be processed from %s on.") % (
                self._support_local(log.scheduled_date)
            )
        return _(
            "Waiting for the next run of the sending job: it has not been "
            "processed yet."
        )

    @api.model
    def _support_log_recipient(self, log):
        phone = getattr(log, "whatsapp_phone", False)
        if phone:
            return mask_phone(phone)
        emails = log._parse_recipient_emails() + log.recipient_partner_ids.mapped(
            "email"
        )
        return ", ".join(filter(None, (mask_email(email) for email in emails))) or None

    @api.model
    def _support_log_row(self, log):
        status = getattr(log, "bookai_last_http_status", 0) or None
        return {
            "id": log.id,
            "rule": log.rule_id.name or None,
            "template": log.template_id.name,
            "template_code": log.template_id.code,
            "channel": log.channel,
            "state": log.state,
            "state_label": self._support_label(log, "state"),
            "explanation": self._support_explain_log(log),
            "error": truncate(mask_text(log.error_message or ""), ERROR_CHARS) or None,
            "http_status": status,
            "external_status": log.external_status or None,
            "recipient": self._support_log_recipient(log),
            "origin_model": log.origin_model,
            "origin_id": log.origin_res_id,
            "created": self._support_local(log.create_date),
            "scheduled": self._support_local(log.scheduled_date),
            "sent": self._support_local(log.sent_date),
        }

    @guarded_tool(
        input_schema=FOLIO_INPUTS,
        output_schema={
            "folio": {"type": "object"},
            "counts": {"type": "array", "items": {"type": "object"}},
            "notifications": {"type": "array", "items": {"type": "object"}},
        },
        log_result=False,
    )
    def _ai_pms_folio_notifications(self, folio_id=None, folio_code=None):
        folio = self._support_find_folio(folio_id, folio_code)
        logs = self.env["pms.notification.log"].search(
            self._support_notification_domain(folio)
        )
        warnings = []
        rows = [
            self._support_log_row(log)
            for log in self._ai_limit_rows(list(logs), warnings, MAX_LOGS)
        ]
        labels = self._support_labels("pms.notification.log", "state")
        counts = Counter(logs.mapped("state"))
        limit = fields.Datetime.now() - timedelta(hours=OVERDUE_HOURS)
        overdue = logs.filtered(
            lambda log: log.state in ("pending", "scheduled")
            and (log.scheduled_date or log.create_date) < limit
        )
        if overdue:
            warnings.append(
                _(
                    "%(count)s logs are still waiting although they were due "
                    "more than %(hours)s hours ago: the sending job may not be "
                    "running."
                )
                % {"count": len(overdue), "hours": OVERDUE_HOURS}
            )
        return {
            "summary": _(
                "%(total)s notification logs for the folio %(code)s: %(states)s."
            )
            % {
                "total": len(logs),
                "code": folio.name,
                "states": ", ".join(
                    f"{count} {labels.get(state, state).lower()}"
                    for state, count in counts.items()
                )
                or _("none"),
            },
            "definitions": [
                _(
                    "Logs of the folio: the ones triggered by the folio or by "
                    "one of its reservations, plus (with BookAI) the ones "
                    "whose origin folio is this one, such as notices about "
                    "invoices or payments."
                ),
                _(
                    "Rule: the notification rule that created the log; empty "
                    "for a manual send or when the rule was deleted."
                ),
                _(
                    "sent means handed over to the channel. Odoo does not know "
                    "the final delivery; for WhatsApp it is only known to "
                    "BookAI."
                ),
                _(
                    "http_status is the answer of BookAI to the last attempt "
                    "(only with BookAI installed); recipient is masked (last "
                    "three digits of the phone, first letter of the e-mail)."
                ),
                _("Times are in the time zone %s.") % self._ai_timezone(),
            ],
            "warnings": warnings,
            "result": {
                "folio": {
                    "id": folio.id,
                    "code": folio.name,
                    "property": folio.pms_property_id.name,
                },
                "counts": [
                    {"state": state, "label": labels.get(state, state), "count": count}
                    for state, count in counts.items()
                ],
                "notifications": rows,
            },
        }
