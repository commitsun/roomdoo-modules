# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import fields, models


class ChannelWubookBackend(models.Model):
    _inherit = "channel.wubook.backend"

    flatten_window_days = fields.Integer(
        help=(
            "Forward window (in days from today) used when exporting a pricelist "
            "marked as 'Flatten to Daily on Wubook' triggered by a change on its "
            "own rules. The actual end date is capped by the latest dated rule "
            "found on the parent chain. It is also the window of the prices of "
            "the room types derived from another room type pushed to the other "
            "pricelists when the derivation changes or the pricelists are "
            "exported, capped by the latest dated rule of the pricelist."
        ),
    )

    def export_pricelists(self, *args, **kwargs):
        res = super().export_pricelists(*args, **kwargs)
        for rec in self:
            backend = rec.with_user(rec.user_id) if rec.user_id else rec
            # The export of the pricelists only sends their items: push the
            # prices of the room types deriving their price as well
            backend._export_derived_prices()
        return res

    def _export_derived_prices(self):
        """Enqueue the push of the prices of the room types deriving their
        price on every regular plan of the backend that has some."""
        self.ensure_one()
        self.env["channel.wubook.product.pricelist"].search(
            [
                ("backend_id", "=", self.id),
                ("external_id", "!=", False),
                "|",
                ("pms_property_ids", "=", False),
                ("pms_property_ids", "in", self.pms_property_id.ids),
            ]
        )._export_derived_prices_delayed()
