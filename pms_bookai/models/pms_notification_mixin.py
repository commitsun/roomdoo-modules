"""
pms_bookai/models/pms_notification_mixin.py

With the final approach:
- phone/lang/display_name/origin_folio are resolved from the bookai_*_tmpl
  template fields
- the log is populated on create (in pms.notification.log)

Only small utility helpers remain here.
"""
from datetime import date, datetime

from odoo import fields, models


class PmsNotificationMixin(models.AbstractModel):
    _inherit = "pms.notification.mixin"

    def _pms_bookai_to_ymd(self, value):
        if not value:
            return False
        if isinstance(value, datetime):
            return fields.Date.to_string(value.date())
        if isinstance(value, date):
            return fields.Date.to_string(value)
        return str(value)[:10]
