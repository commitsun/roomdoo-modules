# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging

from odoo import _, api, fields, models
from odoo.tools.misc import formatLang

from .channel_booking import MIRAI_CHANNEL_ID, get_split_info
from .channel_notes import parse_mirai_club, parse_mirai_upselling
from .retryable import RETRYABLE_ERRORS

_logger = logging.getLogger(__name__)


class ChannelWubookPmsFolioBinding(models.Model):
    _name = "channel.wubook.pms.folio"
    _inherit = "channel.wubook.binding"
    _inherits = {"pms.folio": "odoo_id"}

    # binding fields
    odoo_id = fields.Many2one(
        comodel_name="pms.folio",
        string="Odoo ID",
        required=True,
        ondelete="cascade",
    )

    wubook_status = fields.Selection(
        selection=[
            ("1", "Confirmed"),
            ("2", "Waiting for approval (only wubook reservations)"),
            ("3", "Refused (only wubook reservations)"),
            ("4", "Accepted (only wubook reservations)"),
            ("5", "Cancelled"),
            ("6", "(Probably not used anymore): cancelled with penalty]"),
            ("7", "Modified (New state, it does not exist on Wubook)"),
        ],
    )

    payment_gateway_fee = fields.Float(
        string="Paid at source",
        default=0,
    )

    @api.model
    def import_data(self, backend_id, date_from, date_to, mark):
        """Prepare the batch import of Folios from Channel"""
        domain = []
        if date_from and date_to:
            domain += [
                ("date_arrival", ">=", date_from),
                ("date_arrival", "<=", date_to),
            ]

        return self.import_batch(backend_record=backend_id, domain=domain)

    @api.model
    def _get_channel_note_lines(self, booking):
        """Return the lines for reception that the channel passes in ``booking``.

        Only Mirai is read: whether the guest is a member of the loyalty club
        and the upselling it applied, which the guest pays at the hotel. The
        lines are rendered in the language of the context, which the caller
        sets to the one of the hotel: the import job may run as any user.
        """
        if booking.get("id_channel") != MIRAI_CHANNEL_ID:
            return []
        ancillary = booking.get("ancillary")
        if not isinstance(ancillary, dict):
            return []
        lines = []
        if parse_mirai_club(ancillary.get("ClubStatus")):
            lines.append(_("Loyalty club member"))
        price = parse_mirai_upselling(ancillary.get("Upselling"))
        split = get_split_info(booking)
        # Every stream of a split booking carries the whole upselling, so it
        # only goes on the first one: it must not be collected twice.
        if price is not None and (split is None or split[0] == 1):
            lines.append(
                _(
                    "Upselling applied: %(amount)s (according to Mirai, the guest"
                    " pays it at the hotel)",
                    amount=self._format_channel_amount(price, booking.get("currency")),
                )
            )
        return lines

    @api.model
    def _format_channel_amount(self, amount, currency_code):
        """Return ``amount`` formatted in the language of the context, with
        the currency the channel booked it in."""
        currency = self.env["res.currency"]
        if currency_code:
            currency = currency.with_context(active_test=False).search(
                [("name", "=", currency_code)], limit=1
            )
        if currency:
            return formatLang(self.env, amount, currency_obj=currency)
        return f"{amount:.2f} {currency_code or ''}".strip()

    @api.model
    def _notify_channel_notes_failure(self, folio):
        """Warn reception, on ``folio``, that its channel lines of the internal
        notes may be outdated (in the language of the context).

        The warning is posted once: every import of the booking fails the
        same way until its cause is fixed. Best effort: it runs while the
        booking is imported, which must go on whatever happens here.
        """
        # Out of the ``try``: if the work the import left pending fails, the
        # import must fail as it would have done without this warning.
        self.env.cr.flush()
        try:
            with self.env.cr.savepoint():
                body = _(
                    "The [Mirai] lines of the internal notes could not be"
                    " updated. The details are in the server log."
                )
                note = self.env.ref("mail.mt_note")
                messages = self.env["mail.message"].sudo()
                if messages.search_count(
                    [
                        ("model", "=", folio._name),
                        ("res_id", "=", folio.id),
                        ("subtype_id", "=", note.id),
                        ("body", "ilike", body),
                    ],
                    limit=1,
                ):
                    return
                folio.sudo().message_post(
                    body=body,
                    subtype_id=note.id,
                    email_from=self.env.user.partner_id.email_formatted
                    or folio.pms_property_id.email_formatted,
                )
        except RETRYABLE_ERRORS:
            raise
        except Exception:
            _logger.exception(
                "Could not warn folio %s about its [Mirai] lines", folio.name
            )
