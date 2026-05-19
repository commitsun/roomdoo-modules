from odoo import models


class PmsFolio(models.Model):
    _inherit = "pms.folio"

    def _bookai_get_precheckin_button_param(self):
        """
        Return URL suffix for WhatsApp button templates:
        <folio_id>/precheckin/<access_token>[/<lang_iso>]
        """
        self.ensure_one()
        # Use _portal_ensure_token() to handle the race condition where the
        # folio access_token is not yet populated when the notification rule
        # fires during folio create/confirm (e.g. event rule on_write).
        access_token = (self._portal_ensure_token() or "").strip()
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
