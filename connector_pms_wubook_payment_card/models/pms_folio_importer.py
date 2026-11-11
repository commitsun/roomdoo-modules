# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from datetime import datetime, timedelta

import pytz

from odoo.addons.component.core import Component

# Wubook deletes the card a week after the check-out
WUBOOK_CARD_RETENTION = timedelta(days=7)


class ChannelWubookPmsFolioImporter(Component):
    _inherit = "channel.wubook.pms.folio.importer"

    def _import_dependencies(self, external_data, external_fields):
        # Last hook that sees the booking: _after_import only gets the binding
        self._payment_card_booking = external_data
        return super()._import_dependencies(external_data, external_fields)

    def _after_import(self, binding):
        result = super()._after_import(binding)
        self._sync_payment_card(binding)
        return result

    def _sync_payment_card(self, binding):
        """Record that Wubook keeps a card for the booking, if it does."""
        booking = getattr(self, "_payment_card_booking", None) or {}
        channel_data = booking.get("channel_data")
        channel_data = channel_data if isinstance(channel_data, dict) else {}
        vcc = channel_data.get("vcc_additional_info")
        vcc = vcc if isinstance(vcc, dict) else {}
        if not booking.get("cc_info") and not vcc:
            return
        folio = binding.odoo_id
        values = {
            "provider": "wubook",
            "wubook_backend_id": self.backend_record.id,
            "channel_code": str(booking.get("reservation_code") or binding.external_id),
            "channel_name": self._payment_card_channel_name(booking),
            "reservation_id": folio.reservation_ids[:1].id,
            "external_reference": booking.get("channel_reservation_code")
            or str(booking.get("reservation_code") or ""),
            "available_until": self._payment_card_available_until(booking, folio),
        }
        values.update(self._payment_card_vcc_values(vcc, folio))
        self.env["pms.folio.payment.card"]._sync_channel_card(folio, values)

    def _payment_card_channel_name(self, booking):
        backend_type = self.backend_record.backend_type_id.child_id
        if not booking.get("id_channel"):
            return backend_type.direct_channel_type_id.name or "Wubook"
        ota = backend_type.ota_ids.filtered(
            lambda x: x.wubook_ota == booking["id_channel"]
        )
        return ota.agency_id.name or "Wubook"

    def _payment_card_available_until(self, booking, folio):
        departure = booking.get("date_departure")
        if not departure:
            checkouts = folio.reservation_ids.mapped("checkout")
            departure = max(checkouts) if checkouts else None
        return departure + WUBOOK_CARD_RETENTION if departure else False

    def _payment_card_vcc_values(self, vcc, folio):
        if not vcc:
            return {}
        timezone = pytz.timezone(folio.pms_property_id.tz or "UTC")

        def to_date(timestamp):
            if not timestamp:
                return False
            return datetime.fromtimestamp(int(timestamp), timezone).date()

        currency = self.env["res.currency"].search(
            [
                ("name", "=", vcc.get("vcc_currency_code")),
                ("active", "in", [True, False]),
            ],
            limit=1,
        )
        return {
            "vcc_valid_from": to_date(vcc.get("vcc_valid_from")),
            "vcc_valid_until": to_date(vcc.get("vcc_valid_til")),
            "vcc_balance": float(vcc.get("vcc_balance") or 0.0),
            "vcc_currency_id": currency.id,
        }
