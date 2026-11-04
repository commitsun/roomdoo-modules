import re

import pytz

from odoo import _, api, models
from odoo.exceptions import AccessError, UserError
from odoo.osv import expression

FOLIO_INPUTS = {
    "folio_id": {
        "type": "integer",
        "minimum": 1,
        "description": "Id of the folio. Give this or folio_code.",
    },
    "folio_code": {
        "type": "string",
        "maxLength": 40,
        "description": "Code of the folio, the number users see (for example "
        "416/26/009337). Give this or folio_id.",
    },
}
# name_get shows "<code> (<number of reservations>)" for group folios.
CODE_SUFFIX = re.compile(r"\s*\(\d+\)$")


class PmsAiTools(models.AbstractModel):
    """Helpers shared by the support tools (folio overview and notifications)."""

    _inherit = "pms.ai.tools"

    @api.model
    def _support_local(self, value):
        """Datetime stored in UTC as ISO text in the time zone of the user."""
        if not value:
            return None
        zone = pytz.timezone(self._ai_timezone())
        return pytz.utc.localize(value).astimezone(zone).isoformat(timespec="minutes")

    @api.model
    def _support_labels(self, model_name, field_name):
        """Labels of a selection field by value, in the language of the user."""
        field = self.env[model_name]._fields[field_name]
        return dict(field._description_selection(self.env))

    @api.model
    def _support_label(self, record, field_name):
        """Label of the value of a selection field of a record."""
        value = record[field_name]
        return self._support_labels(record._name, field_name).get(value, value or "")

    @api.model
    def _support_section(self, label, warnings, builder, default):
        """Run ``builder``; if the user may not read that part, leave it out."""
        try:
            return builder()
        except AccessError:
            warnings.append(
                _("You have no access to %s: that part is left out.") % label
            )
            return default

    @api.model
    def _support_find_folio(self, folio_id=None, folio_code=None):
        """The folio of a property the user can see, by id or by code."""
        code = CODE_SUFFIX.sub("", (folio_code or "").strip())
        if bool(folio_id) == bool(code):
            raise UserError(_("Give either folio_id or folio_code."))
        domain = [("pms_property_id", "in", self._ai_scope_property_ids())]
        domain.append(("id", "=", folio_id) if folio_id else ("name", "=", code))
        folios = self.env["pms.folio"].search(domain, limit=2)
        if not folios:
            raise UserError(
                _("No folio found with that id or code among your properties.")
            )
        if len(folios) > 1:
            raise UserError(
                _("Several folios have the code %(code)s: use folio_id (%(ids)s).")
                % {"code": code, "ids": ", ".join(map(str, folios.ids))}
            )
        return folios

    @api.model
    def _support_notification_domain(self, folio):
        """Notification logs of a folio, however they were linked to it.

        A log points to the record that triggered it (the folio or one of its
        reservations); logs of other origins (an invoice, a payment) reach the
        folio only through the origin folio that BookAI resolves.
        """
        logs = self.env["pms.notification.log"]
        clauses = [
            [("origin_model", "=", "pms.folio"), ("origin_res_id", "=", folio.id)],
            [
                ("origin_model", "=", "pms.reservation"),
                ("origin_res_id", "in", folio.reservation_ids.ids),
            ],
        ]
        if "bookai_origin_folio_id" in logs._fields:
            clauses.append([("bookai_origin_folio_id", "=", folio.id)])
        return expression.OR(clauses)
