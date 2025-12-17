"""
pms_notifications/models/pms_property_notification_rule.py

Property notification rules:
- One rule targets one template and one channel.
- Rules can be event-based (on_create/on_write) or scheduled (cron).
- Base module provides "email" channel only.
  Other modules can extend "channel" selection (e.g., BookAI WhatsApp).

Recipients:
- Rules do NOT decide arbitrary recipients lists.
- For auditing and consistency, rules create logs with:
        - recipient_partner_ids when a default recipient can
                be derived from the origin record
        - otherwise recipient_mode='template' so the
                mail.template can decide recipients
"""

import json
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import safe_eval


class PmsPropertyNotificationRule(models.Model):
    _name = "pms.property.notification.rule"
    _description = "PMS Property Notification Rule"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)

    pms_property_ids = fields.Many2many(
        "pms.property",
        "pms_property_notification_rule_rel",
        "rule_id",
        "pms_property_id",
        string="PMS Properties",
        help=(
            "PMS properties where this notification rule applies. "
            "Empty = all properties."
        ),
    )

    template_id = fields.Many2one(
        "pms.notification.template",
        string="Notification Template",
        ondelete="restrict",
        required=True,
        help=("Notification template to use when this rule is triggered."),
    )

    # -------------------------------------------------------------------------
    # Rule type and target model
    # -------------------------------------------------------------------------
    rule_type = fields.Selection(
        [
            ("event", "Event-based"),
            ("scheduled", "Scheduled"),
        ],
        string="Rule Type",
        required=True,
        default="event",
        help=(
            "Event-based rules are triggered on create/write of the target model. "
            "Scheduled rules are processed by a cron job."
        ),
    )

    target_model_id = fields.Many2one(
        "ir.model",
        string="Target Model",
        required=True,
        ondelete="cascade",
        help=(
            "Model where this rule applies, e.g. pms.folio, " "pms.reservation, etc."
        ),
    )

    target_model_name = fields.Char(
        string="Target Model Name",
        related="target_model_id.model",
        store=True,
        readonly=True,
    )

    # -------------------------------------------------------------------------
    # Event-based rules (create / write)
    # -------------------------------------------------------------------------
    event_type = fields.Selection(
        [
            ("on_create", "On Create"),
            ("on_write", "On Write"),
        ],
        string="Event Type",
        help="Event type for event-based rules. Ignored for scheduled rules.",
    )

    event_domain = fields.Char(
        string="Event Domain",
        default="[]",
        help=(
            "Domain (string) applied AFTER the event. "
            "Only records matching this domain will generate notifications."
        ),
    )

    # -------------------------------------------------------------------------
    # Scheduled rules (cron + time + domain)
    # -------------------------------------------------------------------------
    time_field_name = fields.Char(
        string="Time Field Name",
        help=(
            "Technical name of the datetime/date field used as reference "
            "for scheduled rules."
        ),
    )

    offset_days = fields.Integer(
        string="Offset Days",
        help=(
            "Days offset relative to the time field "
            "(negative=before, positive=after)."
        ),
    )

    offset_hours = fields.Integer(
        string="Offset Hours",
        help="Hours offset relative to the time field.",
    )

    time_window_minutes = fields.Integer(
        string="Time Window (minutes)",
        default=60,
        help=(
            "Size of the time window used by the cron to decide "
            "which records should be processed now."
        ),
    )

    scheduled_domain = fields.Char(
        string="Scheduled Domain",
        default="[]",
        help=(
            "Domain (string) applied on the target model "
            "when running this scheduled rule."
        ),
    )

    # -------------------------------------------------------------------------
    # Generic behavior
    # -------------------------------------------------------------------------
    channel = fields.Selection(
        [
            ("email", "Email"),
        ],
        string="Channel",
        default="email",
        required=True,
        help=(
            "Channel used by this rule. Other modules may extend "
            "this selection to add more channels."
        ),
    )

    send_immediately = fields.Boolean(
        string="Send Immediately",
        help=("If enabled, logs created by this rule will be sent immediately."),
    )

    max_sends_per_record = fields.Integer(
        string="Max Sends per Record",
        default=0,
        help=(
            "Maximum number of logs this rule can generate for the same origin record. "
            "0 = no limit."
        ),
    )

    # -------------------------------------------------------------------------
    # Onchange / constraints
    # -------------------------------------------------------------------------
    @api.onchange("template_id")
    def _onchange_template_id_set_target_model(self):
        """Keep target model consistent with the selected template model."""
        for rule in self:
            if rule.template_id and rule.template_id.model_id:
                rule.target_model_id = rule.template_id.model_id

    @api.constrains("template_id", "target_model_id")
    def _check_template_model_matches_target(self):
        """Prevent misconfiguration: rule target model must match template model."""
        for rule in self:
            if rule.template_id and rule.template_id.model_id and rule.target_model_id:
                if rule.template_id.model_id != rule.target_model_id:
                    raise ValidationError(
                        _(
                            "Rule '%(rule)s' targets '%(target)s' but template "
                            "'%(tpl)s' is configured for '%(tpl_model)s'."
                        )
                        % {
                            "rule": rule.name,
                            "target": rule.target_model_id.display_name,
                            "tpl": rule.template_id.name,
                            "tpl_model": rule.template_id.model_id.display_name,
                        }
                    )

    @api.constrains("rule_type", "event_type", "event_domain")
    def _check_event_rule_requirements(self):
        """Event rules must have event_type and a parsable event_domain."""
        for rule in self:
            if rule.rule_type != "event":
                continue
            if not rule.event_type:
                raise ValidationError(
                    _("Event-based rules require an Event Type (on_create/on_write).")
                )
            try:
                safe_eval(rule.event_domain or "[]")
            except Exception as err:
                raise ValidationError(
                    _("Invalid Event Domain on rule '%s'.") % rule.name
                ) from err

    @api.constrains("rule_type", "time_field_name", "scheduled_domain")
    def _check_scheduled_rule_requirements(self):
        """Scheduled rules must have time_field_name and a parsable scheduled_domain."""
        for rule in self:
            if rule.rule_type != "scheduled":
                continue
            if not rule.time_field_name:
                raise ValidationError(_("Scheduled rules require a Time Field Name."))
            try:
                safe_eval(rule.scheduled_domain or "[]")
            except Exception as err:
                raise ValidationError(
                    _("Invalid Scheduled Domain on rule '%s'.") % rule.name
                ) from err

    @api.constrains("channel", "template_id")
    def _check_email_channel_requires_mail_template(self):
        """When channel=email, the template must have mail_template_id."""
        for rule in self:
            if rule.channel == "email":
                if not rule.template_id or not rule.template_id.mail_template_id:
                    raise ValidationError(
                        _(
                            "When channel is 'Email', the selected notification "
                            "template must have an associated mail template."
                        )
                    )

    # -------------------------------------------------------------------------
    # Domain checks and max sends helpers (used by mixin and scheduled runner)
    # -------------------------------------------------------------------------
    def _record_matches_event_domain(self, rec):
        """Return True if rec matches this rule's event_domain."""
        self.ensure_one()
        if not self.event_domain:
            return True
        try:
            dom = safe_eval(self.event_domain) or []
            if not isinstance(dom, list | tuple):
                return True
        except Exception:
            return False
        # Evaluate on current record only
        return bool(rec.search_count([("id", "=", rec.id)] + list(dom)))

    def _is_under_max_sends(self, rec):
        """Check max_sends_per_record for a given origin record."""
        self.ensure_one()
        if not self.max_sends_per_record:
            return True

        Log = self.env["pms.notification.log"].sudo()
        count = Log.search_count(
            [
                ("rule_id", "=", self.id),
                ("origin_model", "=", rec._name),
                ("origin_res_id", "=", rec.id),
                ("state", "!=", "cancelled"),
            ]
        )
        return count < self.max_sends_per_record

    def _build_log_name(self, rec):
        """Human-friendly log name."""
        self.ensure_one()
        template_label = (
            self.template_id.code or self.template_id.name or "Notification"
        )
        rec_label = getattr(rec, "name", False) or rec.display_name or str(rec.id)
        return f"{template_label} / {rec_label}"

    # -------------------------------------------------------------------------
    # Scheduled rules runner (cron entry point)
    # -------------------------------------------------------------------------
    @api.model
    def run_scheduled_rules(self):
        """Entry point for the cron job to process scheduled rules."""
        now = fields.Datetime.now()
        rules = self.search([("rule_type", "=", "scheduled"), ("active", "=", True)])
        for rule in rules:
            rule._run_one_scheduled_rule(now)

    def _run_one_scheduled_rule(self, now):
        """Process a single scheduled rule at the given time."""
        self.ensure_one()

        if not self.target_model_name or not self.time_field_name:
            return

        candidates = self._scheduled_get_candidates(now)
        if not candidates:
            return

        prop_map = self._scheduled_prepare_property_map(candidates)
        records = self._scheduled_filter_by_properties(candidates, prop_map)
        if not records:
            return

        records = self._scheduled_filter_by_max_sends(records)
        if not records:
            return

        self._scheduled_create_logs_and_send(now, records, prop_map)

    # ------------------------------------------------------------
    # Candidates
    # ------------------------------------------------------------
    def _scheduled_get_candidates(self, now):
        TargetModel = self.env[self.target_model_name]
        domain = self._scheduled_build_domain(now)
        return TargetModel.search(domain)

    def _scheduled_build_domain(self, now):
        return self._scheduled_get_scheduled_domain() + self._scheduled_get_time_domain(
            now
        )

    def _scheduled_get_scheduled_domain(self):
        if not self.scheduled_domain:
            return []
        try:
            domain = safe_eval(self.scheduled_domain) or []
            return domain if isinstance(domain, list | tuple) else []
        except Exception:
            return []

    def _scheduled_get_time_domain(self, now):
        lower_bound, upper_bound = self._scheduled_compute_time_bounds(now)
        time_field = self.time_field_name
        return [
            (time_field, ">=", fields.Datetime.to_string(lower_bound)),
            (time_field, "<", fields.Datetime.to_string(upper_bound)),
        ]

    def _scheduled_compute_time_bounds(self, now):
        window_minutes = self.time_window_minutes or 60
        window_start = now
        window_end = now + timedelta(minutes=window_minutes)

        offset_delta = timedelta(
            days=self.offset_days or 0, hours=self.offset_hours or 0
        )
        lower_bound = window_start - offset_delta
        upper_bound = window_end - offset_delta
        return lower_bound, upper_bound

    # ------------------------------------------------------------
    # Property filtering
    # ------------------------------------------------------------
    def _scheduled_prepare_property_map(self, records):
        """Compute property once per record (best effort)."""
        prop_map = {}
        for rec in records:
            prop_map[rec.id] = self._scheduled_get_record_property(rec)
        return prop_map

    def _scheduled_get_record_property(self, rec):
        # Prefer mixin if present
        if hasattr(rec, "_pms_notification_get_property"):
            return rec._pms_notification_get_property()

        # Fallbacks for models not inheriting the mixin
        if hasattr(rec, "pms_property_id") and rec.pms_property_id:
            return rec.pms_property_id
        if hasattr(rec, "property_id") and rec.property_id:
            return rec.property_id
        return False

    def _scheduled_filter_by_properties(self, candidates, prop_map):
        allowed_prop_ids = (
            set(self.pms_property_ids.ids) if self.pms_property_ids else None
        )

        def _keep(rec):
            prop = prop_map.get(rec.id)
            if not prop:
                return False
            if allowed_prop_ids is None:
                return True
            return prop.id in allowed_prop_ids

        return candidates.filtered(_keep)

    # ------------------------------------------------------------
    # Max sends
    # ------------------------------------------------------------
    def _scheduled_filter_by_max_sends(self, records):
        if not self.max_sends_per_record:
            return records

        Log = self.env["pms.notification.log"].sudo()
        domain = [
            ("rule_id", "=", self.id),
            ("origin_model", "=", records._name),
            ("origin_res_id", "in", records.ids),
            ("state", "!=", "cancelled"),
        ]

        grouped = Log.read_group(domain, ["origin_res_id"], ["origin_res_id"])
        counts = {
            g["origin_res_id"][0]: g["__count"]
            for g in grouped
            if g.get("origin_res_id")
        }

        allowed_ids = [
            rid for rid in records.ids if counts.get(rid, 0) < self.max_sends_per_record
        ]
        return records.browse(allowed_ids)

    # ------------------------------------------------------------
    # Logs + sending
    # ------------------------------------------------------------
    def _scheduled_create_logs_and_send(self, now, records, prop_map):
        Log = self.env["pms.notification.log"].sudo()

        for rec in records:
            prop = prop_map.get(rec.id)
            vals_log = self._scheduled_build_log_vals(now, rec, prop)
            log = Log.create(vals_log)

            if self.send_immediately:
                log.action_send_by_channel()

    def _scheduled_build_log_vals(self, now, rec, prop):
        ctx_json = self._scheduled_get_context_json(rec)

        # Try to derive default recipients from the record.
        recipients = self._scheduled_get_recipient_partners(rec)
        recipient_mode = "partners" if recipients else "template"

        vals = {
            "name": self._build_log_name(rec),
            "state": "pending",
            "property_id": prop.id if prop else False,
            "template_id": self.template_id.id,
            "rule_id": self.id,
            "channel": self.channel,
            "scheduled_date": now,
            "context_json": ctx_json,
            "origin_model": rec._name,
            "origin_res_id": rec.id,
            "recipient_mode": recipient_mode,
            "recipient_partner_ids": [(6, 0, recipients.ids)]
            if recipients
            else [(6, 0, [])],
            "recipient_emails": False,
        }
        return vals

    def _scheduled_get_context_json(self, rec):
        ctx_dict = {}
        if hasattr(rec, "_pms_notification_get_context_dict"):
            ctx_dict = rec._pms_notification_get_context_dict() or {}
        elif hasattr(rec, "_get_notification_context"):
            ctx_dict = rec._get_notification_context() or {}
        return json.dumps(ctx_dict if isinstance(ctx_dict, dict) else {})

    def _scheduled_get_recipient_partners(self, rec):
        """Best-effort recipients for scheduled logs."""
        if hasattr(rec, "_pms_notification_get_default_recipient_partners"):
            return rec._pms_notification_get_default_recipient_partners()
        if hasattr(rec, "partner_id") and rec.partner_id:
            return rec.partner_id
        return self.env["res.partner"]
