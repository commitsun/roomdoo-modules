# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import re

from odoo import _, fields, models

from odoo.addons.pms_payment_card import card_crypto, card_utils
from odoo.addons.pms_payment_card.exceptions import (
    CardNotAvailable,
    ChannelManagerUnavailable,
)

# fetch_ccard answers this when the booking has no card, or no longer has it
NO_CARD = -17


def parse_wubook_card(data):
    """Turn a ``fetch_ccard`` answer into the fields of a card."""
    data = data if isinstance(data, dict) else {}
    match = re.match(
        r"^\s*(\d{1,2})\s*/\s*(\d{4}|\d{2})\s*$", str(data.get("cc_expiring") or "")
    )
    month, year = (int(match.group(1)), int(match.group(2))) if match else (0, 0)
    if match and len(match.group(2)) == 2:
        year += 2000
    return {
        "holder": str(data.get("cc_owner") or "").strip(),
        "pan": card_utils.normalize_pan(str(data.get("cc_number") or "")),
        "expiry_month": month,
        "expiry_year": year,
        "cvv": str(data.get("cc_cvv") or "").strip(),
    }


class PmsFolioPaymentCard(models.Model):
    _inherit = "pms.folio.payment.card"

    wubook_backend_id = fields.Many2one(
        comodel_name="channel.wubook.backend",
        ondelete="set null",
    )

    def _fetch_channel_card_wubook(self, private_key):
        self.ensure_one()
        backend = self.wubook_backend_id.sudo()
        if not backend:
            raise ChannelManagerUnavailable(
                _("The Wubook backend of this card no longer exists.")
            )
        if not backend.payment_card_fetch_enabled:
            raise ChannelManagerUnavailable(
                _("Reading cards from Wubook is disabled for this property.")
            )
        if not backend.payment_card_password:
            raise ChannelManagerUnavailable(
                _(
                    "The Wubook card password is missing. Ask an administrator "
                    "to enter it in the Wubook backend."
                )
            )
        vault = self.env["pms.payment.card.vault"]._get_for_property(
            self.pms_property_id
        )
        try:
            card_password = vault._open(
                private_key, backend, backend.payment_card_password
            ).decode()
        except card_crypto.SealedValueError:
            raise ChannelManagerUnavailable(
                _(
                    "The Wubook card password has to be entered again in the "
                    "Wubook backend."
                )
            ) from None
        code, data = backend._payment_card_fetch(self.channel_code, card_password)
        if code == NO_CARD:
            raise CardNotAvailable(_("Wubook no longer keeps this card."))
        if code != 0:
            raise ChannelManagerUnavailable(
                _("Wubook refused to give the card (error %(code)s).", code=code)
            )
        return parse_wubook_card(data)
