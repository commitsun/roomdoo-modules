import re

from odoo import _, models
from odoo.exceptions import ValidationError


class ResPartner(models.Model):
    _inherit = "res.partner"

    def get_whatsapp_phone(self):
        """
        Return the normalized phone in E.164-like format, starting with '+'.

        Accepts:
        - '+34 600 000 000'
        - '0034 600000000' -> '+34600000000'
        """
        self.ensure_one()

        phone = (self.mobile or self.phone or "").strip()
        if not phone:
            raise ValidationError(
                _("Partner %s has no phone/mobile.") % (self.display_name)
            )

        phone = re.sub(r"[ \t\r\n\-\(\)\.]", "", phone)
        if phone.startswith("00"):
            phone = "+" + phone[2:]

        if not phone.startswith("+"):
            raise ValidationError(
                _(
                    "Partner %s phone must include country prefix "
                    "(e.g. +34...). Got: %s"
                )
                % (self.display_name, phone)
            )
        return phone

    def get_whatsapp_country(self):
        self.ensure_one()
        if not self.country_id or not self.country_id.code:
            raise ValidationError(
                _("Partner %s has no country set.") % (self.display_name)
            )
        return (self.country_id.code or "").upper()
