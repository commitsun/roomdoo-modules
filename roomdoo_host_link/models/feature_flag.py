from odoo import api, models


class FeatureFlag(models.Model):
    """Flags are few: every change sends the whole set, host replaces it."""

    _inherit = "feature.flag"

    @api.model_create_multi
    def create(self, vals_list):
        flags = super().create(vals_list)
        self._notify_flags()
        return flags

    def write(self, vals):
        result = super().write(vals)
        self._notify_flags()
        return result

    def unlink(self):
        result = super().unlink()
        self._notify_flags()
        return result

    def _notify_flags(self):
        link = self.env["roomdoo.host.link"]
        link._notify("flags", link._flag_data())
