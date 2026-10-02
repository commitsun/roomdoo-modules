from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PmsProperty(models.Model):
    _inherit = "pms.property"

    satisfaction_survey_enabled = fields.Boolean(
        string="Send satisfaction survey",
        default=False,
        help="If enabled, a satisfaction survey will be sent to the main "
        "folio partner once every reservation in the folio is checked out.",
    )
    satisfaction_survey_id = fields.Many2one(
        comodel_name="survey.survey",
        string="Satisfaction survey",
        default=lambda self: self._default_satisfaction_survey_id(),
        domain="[('active', '=', True)]",
        help="Survey sent to guests after checkout. "
        "Defaults to the survey shipped by this module.",
    )
    satisfaction_survey_send_moment = fields.Selection(
        selection=[
            ("on_checkout", "On checkout"),
            ("after_checkout", "Hours after checkout"),
        ],
        string="Send moment",
        default="on_checkout",
        required=True,
    )
    satisfaction_survey_send_delay_hours = fields.Integer(
        string="Delay (hours)",
        default=0,
        help="Hours to wait after checkout before scheduling the survey email. "
        "Only used when 'Send moment' is 'Hours after checkout'.",
    )

    @api.model
    def _default_satisfaction_survey_id(self):
        return self.env.ref(
            "pms_satisfaction_survey.survey_pms_satisfaction_default",
            raise_if_not_found=False,
        )

    @api.constrains(
        "satisfaction_survey_enabled",
        "satisfaction_survey_id",
        "satisfaction_survey_send_moment",
        "satisfaction_survey_send_delay_hours",
    )
    def _check_satisfaction_survey_settings(self):
        for prop in self:
            if not prop.satisfaction_survey_enabled:
                continue
            if not prop.satisfaction_survey_id:
                raise ValidationError(
                    _(
                        "Property %s has satisfaction surveys enabled but no "
                        "survey is selected."
                    )
                    % prop.display_name
                )
            if (
                prop.satisfaction_survey_send_moment == "after_checkout"
                and prop.satisfaction_survey_send_delay_hours <= 0
            ):
                raise ValidationError(
                    _(
                        "Property %s: delay must be greater than zero when "
                        "sending the survey after checkout."
                    )
                    % prop.display_name
                )
