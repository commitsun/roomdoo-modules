from odoo import models


class PmsFolio(models.Model):
    _inherit = "pms.folio"

    def _bookai_get_precheckin_button_param(self):
        """
        Return URL suffix for WhatsApp button templates:
        <folio_id>/precheckin/<access_token>[/<lang_iso>]
        """
        self.ensure_one()
        access_token = (self.access_token or "").strip()
        if not access_token:
            return ""

        lang_candidate = self.env.context.get("lang") or self.env.user.lang or ""
        lang_iso = ""
        if lang_candidate:
            lang_rec = self.env["res.lang"].search(
                [
                    ("active", "=", True),
                    "|",
                    ("code", "=", lang_candidate),
                    ("iso_code", "=", lang_candidate),
                ],
                limit=1,
            )
            lang_iso = (lang_rec.iso_code or "").strip()

        suffix = f"{self.id}/precheckin/{access_token}"
        if lang_iso:
            suffix = f"{suffix}/{lang_iso}"
        return suffix
