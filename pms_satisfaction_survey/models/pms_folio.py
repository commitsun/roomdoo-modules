import logging
from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

SURVEY_INVITE_TEMPLATE_XMLID = "survey.mail_template_user_input_invite"


class PmsFolio(models.Model):
    _inherit = "pms.folio"

    satisfaction_survey_user_input_id = fields.Many2one(
        comodel_name="survey.user_input",
        string="Satisfaction survey response",
        readonly=True,
        copy=False,
        ondelete="set null",
    )
    satisfaction_survey_user_input_count = fields.Integer(
        compute="_compute_satisfaction_survey_user_input_count",
    )

    @api.depends("satisfaction_survey_user_input_id")
    def _compute_satisfaction_survey_user_input_count(self):
        for folio in self:
            folio.satisfaction_survey_user_input_count = (
                1 if folio.satisfaction_survey_user_input_id else 0
            )

    def action_view_satisfaction_survey(self):
        self.ensure_one()
        user_input = self.satisfaction_survey_user_input_id
        if not user_input:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": _("Satisfaction Survey"),
            "res_model": "survey.user_input",
            "res_id": user_input.id,
            "view_mode": "form",
            "target": "current",
        }

    def _satisfaction_survey_should_schedule(self):
        """Return True if the folio is eligible to schedule a satisfaction survey."""
        self.ensure_one()
        if self.satisfaction_survey_user_input_id:
            return False
        prop = self.pms_property_id
        if not prop or not prop.satisfaction_survey_enabled:
            return False
        if not prop.satisfaction_survey_id:
            return False
        active_reservations = self.reservation_ids.filtered(
            lambda r: r.state != "cancel"
        )
        if not active_reservations:
            return False
        if any(r.state != "done" for r in active_reservations):
            return False
        if not self.email:
            _logger.info(
                "Folio %s eligible for satisfaction survey but has no email; skipping.",
                self.display_name,
            )
            return False
        return True

    def _satisfaction_survey_scheduled_date(self):
        """Return scheduled_date for the mail.mail per property settings."""
        self.ensure_one()
        prop = self.pms_property_id
        now = fields.Datetime.now()
        if prop.satisfaction_survey_send_moment == "after_checkout":
            return now + timedelta(hours=prop.satisfaction_survey_send_delay_hours)
        return now

    def _try_schedule_satisfaction_survey(self):
        """Create a survey.user_input for the folio and queue the invitation email.

        Idempotent: does nothing if a user_input already exists for the folio.
        """
        invite_template = self.env.ref(
            SURVEY_INVITE_TEMPLATE_XMLID, raise_if_not_found=False
        )
        if not invite_template:
            _logger.warning(
                "Survey invite mail template '%s' not found; cannot send "
                "satisfaction surveys.",
                SURVEY_INVITE_TEMPLATE_XMLID,
            )
            return
        for folio in self:
            if not folio._satisfaction_survey_should_schedule():
                continue
            survey = folio.pms_property_id.satisfaction_survey_id
            scheduled_date = folio._satisfaction_survey_scheduled_date()
            user_input = survey.sudo()._create_answer(
                partner=folio.partner_id or False,
                email=folio.email,
                check_attempts=False,
                **{"folio_id": folio.id},
            )
            lang = folio.lang or self.env.lang
            invite_template.with_context(lang=lang).send_mail(
                user_input.id,
                email_values={"scheduled_date": scheduled_date},
                force_send=False,
            )
            folio.satisfaction_survey_user_input_id = user_input
            _logger.info(
                "Satisfaction survey scheduled for folio %s (user_input %s, "
                "scheduled_date %s).",
                folio.display_name,
                user_input.id,
                scheduled_date,
            )
