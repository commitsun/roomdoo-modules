"""
pms_notifications/models/pms_notification_mixin.py

Mixin used by business models (pms.folio, pms.reservation, account.move, etc.)
to trigger notifications.

Key responsibilities:
- Determine property context for a record
- Run event rules (on_create / on_write)
- Create notification logs with unified recipients

Manual sending:
- `_pms_notification_send_manual(...)` creates a log based on wizard input.
  It is channel-agnostic, and only supports base channels.
  Extensions (pms_bookai) may override it to implement per-channel splitting behavior.
"""

import json
import logging

from odoo import models

_logger = logging.getLogger(__name__)


class PmsNotificationMixin(models.AbstractModel):
    _name = "pms.notification.mixin"
    _description = "PMS Notification Mixin"

    # -------------------------------------------------------------------------
    # Property resolution
    # -------------------------------------------------------------------------
    def _pms_notification_get_property(self):
        """
        Best-effort property resolution.
        Override in models if you have special logic.
        """
        self.ensure_one()
        if "pms_property_id" in self._fields and self.pms_property_id:
            return self.pms_property_id
        if "property_id" in self._fields and self.property_id:
            return self.property_id
        return False

    # -------------------------------------------------------------------------
    # Recipient resolution (rules)
    # -------------------------------------------------------------------------
    def _pms_notification_get_default_recipient_partners(self):
        """
        Best-effort default partner recipients for rule-based notifications.
        Override per model if needed.
        """
        self.ensure_one()
        if "partner_id" in self._fields and self.partner_id:
            return self.partner_id
        return self.env["res.partner"]

    # -------------------------------------------------------------------------
    # Context for log creation
    # -------------------------------------------------------------------------
    def _pms_notification_get_context_dict(self):
        """
        Optional hook for business models to provide context.
        This is stored in log.context_json.
        """
        self.ensure_one()
        if hasattr(self, "_get_notification_context"):
            ctx = self._get_notification_context() or {}
            return ctx if isinstance(ctx, dict) else {}
        return {}

    # -------------------------------------------------------------------------
    # Event rules runner
    # -------------------------------------------------------------------------
    def _pms_notification_run_event_rules(
        self,
        event_type,
        changed_fields=None,
        pre_domain_matches=None,
    ):
        """
        Run all matching event rules for current records.
        Called by create/write overrides on business models.
        """
        if not self:
            return True

        Rule = self.env["pms.property.notification.rule"].sudo()
        Log = self.env["pms.notification.log"].sudo()

        target_model = self._name

        rules = Rule.search(
            [
                ("active", "=", True),
                ("rule_type", "=", "event"),
                ("event_type", "=", event_type),
                ("target_model_name", "=", target_model),
            ]
        )
        if not rules:
            return True

        for rec in self:
            prop = rec._pms_notification_get_property()

            for rule in rules:
                # Property filtering: empty = applies to all
                if rule.pms_property_ids and (
                    not prop or prop.id not in rule.pms_property_ids.ids
                ):
                    continue

                if event_type == "on_write" and not rule._event_matches_changed_fields(
                    changed_fields
                ):
                    continue

                if event_type == "on_write" and rule._has_event_pre_domain():
                    if pre_domain_matches is not None:
                        matched_ids = pre_domain_matches.get(rule.id, set())
                        if rec.id not in matched_ids:
                            continue

                # Evaluate event domain against the *record after* create/write
                if not rule._record_matches_event_domain(rec):
                    continue

                if (
                    rec._name == "pms.folio"
                    and not rule.template_id._is_applicable_to_folio(rec)
                ):
                    continue

                # Max sends per record (per rule)
                if not rule._is_under_max_sends(rec):
                    continue

                recipients = rec._pms_notification_get_default_recipient_partners()
                recipient_mode = "partners" if recipients else "template"

                vals = {
                    "name": rule._build_log_name(rec),
                    "state": "pending",
                    "property_id": prop.id if prop else False,
                    "template_id": rule.template_id.id,
                    "rule_id": rule.id,
                    "channel": rule.channel,
                    "scheduled_date": False,
                    "context_json": json.dumps(
                        rec._pms_notification_get_context_dict()
                    ),
                    "origin_model": rec._name,
                    "origin_res_id": rec.id,
                    "recipient_mode": recipient_mode,
                    "recipient_partner_ids": [(6, 0, recipients.ids)]
                    if recipients
                    else [(6, 0, [])],
                    "recipient_emails": False,
                }

                log = Log.create(vals)

                if rule.send_immediately:
                    log.action_send_by_channel()

        return True

    def _pms_notification_prepare_pre_domain_matches(
        self,
        event_type,
        changed_fields=None,
    ):
        """
        Prepare rule->record ids map for event pre-domains before write.

        This must be called before the write when event_type='on_write'.
        """
        if not self or event_type != "on_write":
            return {}

        Rule = self.env["pms.property.notification.rule"].sudo()
        rules = Rule.search(
            [
                ("active", "=", True),
                ("rule_type", "=", "event"),
                ("event_type", "=", event_type),
                ("target_model_name", "=", self._name),
            ]
        )
        if not rules:
            return {}

        matches = {}
        for rule in rules:
            if not rule._has_event_pre_domain():
                continue
            if not rule._event_matches_changed_fields(changed_fields):
                continue
            matched = self.filtered_domain(rule._get_event_pre_domain())
            matches[rule.id] = set(matched.ids)

        return matches

    # -------------------------------------------------------------------------
    # Manual sending API (called by wizard)
    # -------------------------------------------------------------------------
    def _pms_notification_send_manual(
        self,
        template,
        channel,
        send_immediately=True,
        extra_context=None,
        recipient_mode="template",
        recipient_partner_ids=None,
        recipient_emails=None,
    ):
        """
        Create a notification log for a manual send request.

        In base module:
        - Creates a single log (no per-recipient split).
        - Extensions may override (e.g., split WhatsApp by partner).
        """
        self.ensure_one()

        recipient_partner_ids = recipient_partner_ids or []
        recipient_emails = recipient_emails or ""

        prop = self._pms_notification_get_property()
        ctx = self._pms_notification_get_context_dict()
        if extra_context and isinstance(extra_context, dict):
            ctx.update(extra_context)

        Log = self.env["pms.notification.log"].sudo()

        vals = {
            "name": f"{template.code or template.name} / {self.display_name}",
            "state": "pending",
            "property_id": prop.id if prop else False,
            "template_id": template.id,
            "rule_id": False,
            "channel": channel,
            "scheduled_date": False,
            "context_json": json.dumps(ctx),
            "origin_model": self._name,
            "origin_res_id": self.id,
            "recipient_mode": recipient_mode,
            "recipient_partner_ids": [(6, 0, recipient_partner_ids)],
            "recipient_emails": recipient_emails or False,
        }

        log = Log.create(vals)

        if send_immediately:
            log.action_send_by_channel()

        return log
