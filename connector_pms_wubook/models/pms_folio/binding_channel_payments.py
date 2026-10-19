# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Register on the folio the amount the channel reports as paid online."""

from odoo import Command, _, fields, models
from odoo.tools.misc import format_datetime, formatLang


class ChannelWubookPmsFolioBinding(models.Model):
    _inherit = "channel.wubook.pms.folio"

    # What the channel reports, as mapped from the last booking version that
    # carried the information (see channel_payments.channel_payment_values)
    channel_paid_amount = fields.Float(
        string="Paid at Channel",
        help="Amount the channel reports as paid online for the whole booking "
        "(last known value).",
        digits="Account",
        readonly=True,
        copy=False,
    )
    channel_due_amount = fields.Float(
        string="Due at Channel",
        help="Amount the channel reports as still due for the whole booking "
        "(last known value).",
        digits="Account",
        readonly=True,
        copy=False,
    )
    channel_payment_currency = fields.Char(
        help="Currency (ISO code) of the amounts reported by the channel.",
        readonly=True,
        copy=False,
    )
    channel_payment_deadline = fields.Datetime(
        help="Deadline the channel gave the guest to pay the booking online. "
        "It is emptied when the channel reports the payment.",
        readonly=True,
        copy=False,
    )
    channel_payment_split = fields.Char(
        help="Part of the channel booking held by this folio (part/total) "
        "when Wubook split the booking in several folios.",
        readonly=True,
        copy=False,
    )
    # What this sync did
    channel_paid_registered = fields.Float(
        string="Registered from Channel",
        help="Total amount registered automatically on this folio from what "
        "the channel reports as paid online. It never decreases, so payments "
        "cancelled or edited by the staff are never registered again.",
        digits="Account",
        readonly=True,
        copy=False,
    )
    channel_payment_ids = fields.Many2many(
        string="Channel Payments",
        help="Payments registered automatically from what the channel reports "
        "as paid online.",
        comodel_name="account.payment",
        relation="channel_wubook_pms_folio_payment_rel",
        column1="binding_id",
        column2="payment_id",
        readonly=True,
        copy=False,
    )
    channel_payment_notes = fields.Json(
        help="Technical field: last value of every kind of channel payment "
        "note posted on the folio, so that re-imports do not repeat them.",
        readonly=True,
        copy=False,
    )

    def _has_channel_payment_info(self):
        """Whether the channel ever reported anything about the payment."""
        self.ensure_one()
        return bool(self.channel_payment_currency or self.channel_payment_deadline)

    def _get_channel_payment_mapping(self, folio):
        """Return the OTA payment mapping that registers the channel payments
        of ``folio``: the one of its agency flagged with "Register Channel
        Payments". Empty when the channel has not been enabled.
        """
        self.ensure_one()
        if not folio.agency_id:
            return self.env["wubook.backend.journal.ota"]
        return self.sudo().backend_id.backend_journal_ota_ids.filtered(
            lambda row: row.register_channel_payments
            and row.agency_id == folio.agency_id
        )[:1]

    def _sync_channel_payments(self, folio, cancelled_now):
        """Register on ``folio`` what the channel reports as paid online.

        Run by the folio importer after every import of the booking, with
        ``cancelled_now`` telling whether that import cancelled the folio. It
        only registers the increase of the paid amount over what it already
        registered (``channel_paid_registered``, which never decreases) and
        it never cancels, edits nor refunds a payment: whatever it cannot do
        safely is left to the staff with a note on the folio.
        """
        self.ensure_one()
        mapping = self._get_channel_payment_mapping(folio)
        if (
            not mapping
            # the prepayment flow of the importer owns this booking
            or self.payment_gateway_fee > 0
            or not self._has_channel_payment_info()
        ):
            return
        binding = self.with_context(lang=self._get_channel_payment_lang(folio))
        if folio.state != "cancel":
            # a later cancellation of the booking must be noted again
            binding._forget_channel_payment_notes("cancelled", "cancelled_unpaid")
        if (
            binding._hold_split_channel_payment(folio)
            or binding._hold_foreign_currency_channel_payment(folio)
            or binding._hold_cancelled_channel_payment(folio, cancelled_now)
            or binding._hold_pending_channel_payment(folio)
        ):
            return
        amount = binding._get_channel_payment_amount_to_register(folio)
        if (
            not amount
            or binding._hold_channel_payment_on_manual_payments(folio)
            or binding._hold_channel_payment_on_sibling_folios(folio)
        ):
            return
        binding._register_channel_payment(folio, mapping, amount)

    # Rules: every _hold_* method returns True when the payment must not be
    # registered, after leaving a note on the folio when the staff has to
    # know it. They run on the binding in the language of the property.

    def _hold_split_channel_payment(self, folio):
        """Wubook split the booking in several folios and every one of them
        reports the paid amount of the whole booking: the staff, who usually
        merges the parts by hand, registers it on the right folio.
        """
        if not self.channel_payment_split:
            return False
        paid = self.channel_paid_amount
        if paid > 0:
            self._post_channel_payment_note(
                folio,
                "split",
                self._channel_payment_note_key(paid),
                _(
                    "%(channel)s reports %(amount)s paid online for the whole "
                    "booking, which Wubook split in %(parts)s folios. It has "
                    "not been registered automatically: register it by hand "
                    "on the right folio.",
                    channel=folio.agency_id.name,
                    amount=self._format_channel_payment_amount(folio, paid),
                    parts=self.channel_payment_split.split("/")[-1],
                ),
            )
        return True

    def _hold_foreign_currency_channel_payment(self, folio):
        currency = self.channel_payment_currency
        if not currency or currency == folio.currency_id.name:
            return False
        paid = self.channel_paid_amount
        if paid > 0:
            self._post_channel_payment_note(
                folio,
                "currency",
                f"{currency} {self._channel_payment_note_key(paid)}",
                _(
                    "%(channel)s reports %(amount)s paid online, but the folio "
                    "is in %(folio_currency)s. It has not been registered "
                    "automatically.",
                    channel=folio.agency_id.name,
                    amount=self._format_channel_payment_amount(folio, paid),
                    folio_currency=folio.currency_id.name,
                ),
            )
        return True

    def _hold_cancelled_channel_payment(self, folio, cancelled_now):
        """No payment is ever registered on a cancelled folio, and what was
        registered is kept: the penalty and the refund are up to the staff.
        """
        if folio.state != "cancel":
            return False
        paid = self.channel_paid_amount
        registered = self.channel_paid_registered
        channel = folio.agency_id.name
        if cancelled_now and registered > 0:
            self._post_channel_payment_note(
                folio,
                "cancelled",
                self._channel_payment_note_key(registered),
                _(
                    "Booking cancelled in %(channel)s with %(amount)s paid "
                    "online. The payment registered automatically is kept and "
                    "no refund has been registered: review the cancellation "
                    "penalty and the refund.",
                    channel=channel,
                    amount=self._format_channel_payment_amount(folio, registered),
                ),
            )
        elif not registered and paid > 0:
            self._post_channel_payment_note(
                folio,
                "cancelled_unpaid",
                self._channel_payment_note_key(paid),
                _(
                    "Booking cancelled in %(channel)s with %(amount)s paid "
                    "online. The folio is cancelled, so the payment has not "
                    "been registered automatically: review the cancellation "
                    "penalty and the refund.",
                    channel=channel,
                    amount=self._format_channel_payment_amount(folio, paid),
                ),
            )
        return True

    def _hold_pending_channel_payment(self, folio):
        """The guest has not paid yet the booking the channel expects to be
        paid online before a deadline.
        """
        deadline = self.channel_payment_deadline
        if not deadline or self.channel_paid_amount > 0:
            return False
        self._post_channel_payment_note(
            folio,
            "pending",
            fields.Datetime.to_string(deadline),
            _(
                "%(channel)s: online payment pending until %(deadline)s. If "
                "the guest does not pay, the channel will cancel the booking.",
                channel=folio.agency_id.name,
                deadline=format_datetime(
                    self.env,
                    deadline,
                    tz=folio.pms_property_id.tz,
                    dt_format=False,
                ),
            ),
        )
        return True

    def _get_channel_payment_amount_to_register(self, folio):
        """Return what the channel reports as paid and was not registered yet
        (0.0 when nothing). Payments only go up: when the channel reports
        less than what was registered, the staff reviews it.
        """
        currency = folio.currency_id
        paid = self.channel_paid_amount
        registered = self.channel_paid_registered
        amount = currency.round(paid - registered)
        if currency.compare_amounts(amount, 0.0) > 0:
            return amount
        if currency.compare_amounts(paid, registered) < 0:
            self._post_channel_payment_note(
                folio,
                "lower",
                self._channel_payment_note_key(paid),
                _(
                    "%(channel)s now reports %(amount)s paid online, less than "
                    "the %(registered)s registered automatically. Refunds are "
                    "not registered automatically: review the payments.",
                    channel=folio.agency_id.name,
                    amount=self._format_channel_payment_amount(folio, paid),
                    registered=self._format_channel_payment_amount(folio, registered),
                ),
            )
        return 0.0

    def _hold_channel_payment_on_manual_payments(self, folio):
        """Until it registers its first payment, the sync steps aside when
        the staff already registered payments on the folio by hand, so the
        prepayment is never registered twice.
        """
        if self.channel_paid_registered > 0:
            return False
        folio_sudo = folio.sudo()
        manual_payments = (
            folio_sudo.payment_ids.filtered(
                lambda pay: pay.state == "posted" and pay.payment_type == "inbound"
            )
            - self.sudo().channel_payment_ids
        )
        cash_payments = folio_sudo.statement_line_ids.filtered(
            lambda line: line.amount > 0
        )
        if not manual_payments and not cash_payments:
            return False
        paid = self.channel_paid_amount
        self._post_channel_payment_note(
            folio,
            "manual",
            self._channel_payment_note_key(paid),
            _(
                "%(channel)s reports %(amount)s paid online. The folio already "
                "has payments registered by hand, so it has not been "
                "registered automatically to avoid a duplicate.",
                channel=folio.agency_id.name,
                amount=self._format_channel_payment_amount(folio, paid),
            ),
        )
        return True

    def _hold_channel_payment_on_sibling_folios(self, folio):
        """Another folio of the same channel booking (e.g. a duplicate left by
        a modification Wubook could not chain) already got the payment.
        """
        codes = [
            code
            for code in set(folio.reservation_ids.mapped("ota_reservation_code"))
            if code
        ]
        if not codes:
            return False
        siblings = self.sudo().search(
            [
                ("backend_id", "=", self.backend_id.id),
                ("odoo_id", "!=", folio.id),
                ("channel_paid_registered", ">", 0),
                ("reservation_ids.ota_reservation_code", "in", codes),
            ]
        )
        if not siblings:
            return False
        paid = self.channel_paid_amount
        self._post_channel_payment_note(
            folio,
            "sibling",
            self._channel_payment_note_key(paid),
            _(
                "%(channel)s reports %(amount)s paid online for the whole "
                "booking, already registered on another folio of the same "
                "booking (%(folios)s). It has not been registered again.",
                channel=folio.agency_id.name,
                amount=self._format_channel_payment_amount(folio, paid),
                folios=", ".join(siblings.mapped("odoo_id.name")),
            ),
        )
        return True

    def _register_channel_payment(self, folio, mapping, amount):
        """Register ``amount`` on the folio through the journal of the OTA
        payment mapping. The full amount is registered even if it exceeds the
        folio total (the money was charged): a note warns about it.
        """
        payments = self.env["account.payment"].sudo()
        channel = folio.agency_id.name
        before = payments.search([("folio_ids", "in", folio.ids)])
        folio.do_payment(
            mapping.payment_method_line_id,
            self.env.user,
            amount,
            folio,
            partner=folio.partner_id,
            date=fields.Date.context_today(
                folio.with_context(tz=folio.pms_property_id.tz)
            ),
            ref=channel,
        )
        new_payments = payments.search(
            [("folio_ids", "in", folio.ids), ("id", "not in", before.ids)]
        )
        currency = folio.currency_id
        self.write(
            {
                "channel_paid_registered": currency.round(
                    self.channel_paid_registered + amount
                ),
                "channel_payment_ids": [Command.link(pay.id) for pay in new_payments],
            }
        )
        # a later failure must be noted again, even if it is the same one
        self._forget_channel_payment_notes("error")
        paid = self.channel_paid_amount
        self._post_channel_payment_note(
            folio,
            "registered",
            None,
            _(
                "%(channel)s reports %(paid)s paid online: payment of "
                "%(amount)s registered automatically.",
                channel=channel,
                paid=self._format_channel_payment_amount(folio, paid),
                amount=self._format_channel_payment_amount(folio, amount),
            ),
        )
        if currency.compare_amounts(paid, folio.amount_total) > 0:
            self._post_channel_payment_note(
                folio,
                "exceeds",
                self._channel_payment_note_key(paid),
                _(
                    "The amount paid online (%(amount)s) exceeds the folio "
                    "total (%(total)s): it probably includes extras that are "
                    "not in the folio.",
                    amount=self._format_channel_payment_amount(folio, paid),
                    total=formatLang(
                        self.env, folio.amount_total, currency_obj=currency
                    ),
                ),
            )

    def _notify_channel_payment_error(self, folio, error):
        """Leave on the folio the reason why the sync failed (once per reason)."""
        self.ensure_one()
        binding = self.with_context(lang=self._get_channel_payment_lang(folio))
        binding._post_channel_payment_error_note(folio, str(error) or repr(error))

    def _post_channel_payment_error_note(self, folio, reason):
        self._post_channel_payment_note(
            folio,
            "error",
            reason[:200],
            _(
                "The amount %(channel)s reports as paid online could not be "
                "registered automatically (%(error)s). Review the payments of "
                "the folio.",
                channel=folio.agency_id.name,
                error=reason,
            ),
        )

    # Helpers

    def _get_channel_payment_lang(self, folio):
        return (
            folio.pms_property_id.lang
            or folio.company_id.partner_id.lang
            or self.env.lang
        )

    def _format_channel_payment_amount(self, folio, amount):
        currency = folio.currency_id
        code = self.channel_payment_currency
        if not code or code == currency.name:
            return formatLang(self.env, amount, currency_obj=currency)
        return f"{formatLang(self.env, amount)} {code}"

    def _channel_payment_note_key(self, amount):
        return f"{amount:.2f}"

    def _post_channel_payment_note(self, folio, kind, key, body):
        """Post ``body`` as a note on the folio, once per ``kind`` and ``key``.

        A note of a kind is posted again only when its key (the amount or the
        date it is about) changes, so re-imports do not repeat it. With
        ``key`` None the note is always posted.
        """
        if key is not None:
            notes = dict(self.channel_payment_notes or {})
            if notes.get(kind) == key:
                return
            notes[kind] = key
            self.channel_payment_notes = notes
        folio.sudo().message_post(
            body=body,
            subtype_id=self.env.ref("mail.mt_note").id,
            email_from=self.env.user.partner_id.email_formatted
            or folio.pms_property_id.email_formatted,
        )

    def _forget_channel_payment_notes(self, *kinds):
        notes = dict(self.channel_payment_notes or {})
        if any(kind in notes for kind in kinds):
            for kind in kinds:
                notes.pop(kind, None)
            self.channel_payment_notes = notes or False
