from markupsafe import Markup

from odoo import _, fields, models
from odoo.exceptions import UserError

from .account_move import BYPASS_CONTEXT_KEY, BYPASS_GROUP


class LockedPaymentError(UserError):
    """A change that a payment of a locked period cannot take. ``code`` says
    which one (``journal``, ``date``, ``partner``, ``unsupported``), so that an
    API can answer it with a typed problem."""

    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


class AccountPayment(models.Model):
    _inherit = "account.payment"

    amended_payment_id = fields.Many2one(
        comodel_name="account.payment",
        string="Corrected payment",
        readonly=True,
        copy=False,
        index="btree_not_null",
        help="The payment of a locked period this one corrects. That payment is "
        "never modified itself: the difference, or the refund, is registered "
        "with the date of the day and linked to it.",
    )
    amendment_ids = fields.One2many(
        comodel_name="account.payment",
        inverse_name="amended_payment_id",
        string="Corrections",
        readonly=True,
    )

    def action_draft(self):
        self._check_settles_no_rectified_invoice()
        return super().action_draft()

    def _check_settles_no_rectified_invoice(self):
        """Refuse to reset a payment that settles an invoice already rectified.

        An invoice rectified keeping its reconciliations (see
        ``_rectify_keep_reconciliation``) still holds the payment it was
        settled with, while the money its credit note took over has moved on
        to the invoice that replaced it. Resetting the payment unreconciles it,
        so the rectified invoice would owe its amount again with nothing left
        to settle it: the customer would end up charged twice. Whatever the
        period, which is why this is not part of the lock date guards.
        """
        if self.env.context.get(BYPASS_CONTEXT_KEY):
            return True
        for payment in self:
            rectified = payment.reconciled_invoice_ids.filtered(
                lambda move: move.state == "posted"
                and move.move_type in ("out_invoice", "in_invoice")
                and move._is_rectified()
            )
            if not rectified or self.env.user.has_group(BYPASS_GROUP):
                continue
            raise UserError(
                _(
                    "Payment %(payment)s cannot be changed: it settles "
                    "%(invoices)s, which has already been rectified and replaced "
                    "by a new invoice that received its money. Changing the "
                    "payment would make the rectified invoice owe it again. "
                    "Register the difference as a new payment or refund, or ask "
                    "accounting.",
                    payment=payment.display_name,
                    invoices=", ".join(rectified.mapped("display_name")),
                )
            )
        return True

    # ------------------------------------------------------------------
    # payments of a locked period
    # ------------------------------------------------------------------
    def _is_reconcile_locked(self):
        """Whether this payment belongs to a locked period: its own date, or
        the date of anything reconciled with it, is on or before its company's
        lock date.

        A payment of an open month that settles an invoice of a closed one
        counts: resetting it to draft would break a reconciliation of the
        closed period.
        """
        self.ensure_one()
        lock_date = self.company_id._get_reconcile_lock_date()
        lines = self.move_id.line_ids.filtered(
            lambda line: line.account_id.account_type
            in ("asset_receivable", "liability_payable")
        )
        partials = lines.matched_debit_ids | lines.matched_credit_ids
        moves = (
            self.move_id | (partials.debit_move_id | partials.credit_move_id).move_id
        )
        return any(move.date <= lock_date for move in moves)

    def _reconcile_lock_bypassed(self):
        """Whether the payments of a locked period may be changed the usual
        way: by the members of the bypass group, or by code that says so."""
        return bool(
            self.env.context.get(BYPASS_CONTEXT_KEY)
        ) or self.env.user.has_group(BYPASS_GROUP)

    def _amended_amount(self):
        """The amount of this payment once its corrections are counted."""
        self.ensure_one()
        amount = self.amount
        for correction in self.amendment_ids.filtered(lambda p: p.state == "posted"):
            if correction.payment_type == self.payment_type:
                amount += correction.amount
            else:
                amount -= correction.amount
        return (self.currency_id or self.company_id.currency_id).round(amount)

    def _amend_locked_payment(self, vals, correction_vals=None):
        """Apply ``vals`` to this payment of a locked period without resetting
        it to draft. What changes is registered with the date of the day and
        linked to it:

        - ``ref`` is written in place: it is no accounting field.
        - ``amount``: a payment, or a refund, for the difference with the
          amount once its corrections are counted.
        - ``partner_id``: a transfer entry moves its open balance to the new
          customer. A payment that settles an invoice is refused: the customer
          of a payment follows the invoice it settles, whose own edit moves the
          money.
        - ``date``: refused when its own date is locked. A payment locked only
          by what it is reconciled with moves to another open date in place.
        - ``journal_id``, and anything else, is refused.

        ``correction_vals`` is added to the payments it registers (the links of
        the PMS to the folio, for instance). Returns them.
        """
        self.ensure_one()
        vals = dict(vals)
        lock_date = self.company_id._get_reconcile_lock_date()
        unsupported = set(vals) - {"ref", "amount", "partner_id", "date"}
        if "journal_id" in unsupported:
            raise LockedPaymentError(
                _(
                    "Payment %(payment)s belongs to a closed period (locked up to "
                    "%(lock)s): its journal cannot be changed. Ask accounting to "
                    "correct it.",
                    payment=self.name,
                    lock=lock_date,
                ),
                "journal",
            )
        if unsupported:
            raise LockedPaymentError(
                _(
                    "Payment %(payment)s belongs to a closed period (locked up to "
                    "%(lock)s), so it cannot be changed this way. Ask accounting.",
                    payment=self.name,
                    lock=lock_date,
                ),
                "unsupported",
            )
        partner = self.partner_id
        if "partner_id" in vals:
            partner = self.env["res.partner"].browse(vals.pop("partner_id"))
            if partner != self.partner_id:
                self._check_locked_partner_change()
        if "date" in vals:
            self._move_locked_payment_date(vals.pop("date"))
        if partner != self.partner_id:
            self._transfer_locked_payment_partner(partner)
        corrections = self.browse()
        if "amount" in vals:
            corrections |= self._register_locked_payment_difference(
                vals.pop("amount"), partner, correction_vals or {}
            )
        if "ref" in vals:
            self.move_id.ref = vals.pop("ref")
        return corrections

    def _check_locked_partner_change(self):
        """Refuse to change the customer of a payment of a locked period that
        settles something: the customer of a payment follows the invoice it
        settles, and the edit of that invoice is what moves the money."""
        self.ensure_one()
        _liquidity, counterpart, _writeoff = self._seek_for_lines()
        partials = counterpart.matched_debit_ids | counterpart.matched_credit_ids
        if not partials:
            return
        settled = (partials.debit_move_id | partials.credit_move_id).move_id - (
            self.move_id
        )
        invoices = settled.filtered(lambda move: move.is_invoice(include_receipts=True))
        lock_date = self.company_id._get_reconcile_lock_date()
        message = _(
            "Payment %(payment)s belongs to a closed period (locked up to %(lock)s) "
            "and settles %(documents)s: its customer cannot be changed, because "
            "the customer of a payment follows the invoice it settles.",
            payment=self.name,
            lock=lock_date,
            documents=", ".join(settled.mapped("display_name")),
        )
        hints = []
        for invoice in invoices:
            if invoice._is_rectified():
                hints.append(
                    _(
                        "%s has already been rectified: its money is already on "
                        "the invoice that replaced it.",
                        invoice.display_name,
                    )
                )
            elif invoice._is_downpayment():
                hints.append(
                    _(
                        "%s is a down payment: the final invoice takes it to "
                        "whichever customer it is issued to.",
                        invoice.display_name,
                    )
                )
            else:
                hints.append(
                    _(
                        "Change the customer of %s instead: its money moves with "
                        "it.",
                        invoice.display_name,
                    )
                )
        if not invoices:
            hints.append(_("Ask accounting."))
        raise LockedPaymentError(" ".join([message] + hints), "partner")

    def _move_locked_payment_date(self, new_date):
        """Move a payment that is locked only by what it is reconciled with to
        another open date, in place: reset to draft, it would break the
        reconciliation of the closed period. When the month changes it takes
        the next number of the new month, which leaves a gap in the old one."""
        self.ensure_one()
        new_date = fields.Date.to_date(new_date)
        if new_date == self.date:
            return
        lock_date = self.company_id._get_reconcile_lock_date()
        if self.date <= lock_date:
            raise LockedPaymentError(
                _(
                    "Payment %(payment)s is dated within a closed period (locked up "
                    "to %(lock)s): its date cannot be changed. Ask accounting.",
                    payment=self.name,
                    lock=lock_date,
                ),
                "date",
            )
        if new_date <= lock_date:
            raise LockedPaymentError(
                _(
                    "The new date %(date)s falls within a closed period (locked up "
                    "to %(lock)s). Use a later date.",
                    date=new_date,
                    lock=lock_date,
                ),
                "date",
            )
        old_date = self.date
        move = self.move_id.with_context(skip_account_move_synchronization=True)
        same_month = (new_date.year, new_date.month) == (old_date.year, old_date.month)
        move_vals = {"date": new_date}
        if not same_month:
            move_vals["name"] = "/"
        move.write(move_vals)
        move.line_ids.write({"date_maturity": new_date})
        if not same_month:
            move._set_next_sequence()
        self.message_post(
            body=Markup("<p>%s</p>")
            % _(
                "Date moved from %(old)s to %(new)s in place: the payment settles a "
                "document of a closed period, so it was not reset to draft.",
                old=old_date,
                new=new_date,
            )
        )

    def _transfer_locked_payment_partner(self, partner):
        """Move the open balance of this payment of a locked period to
        ``partner`` with a transfer entry dated today. The payment keeps its
        customer, in its period."""
        self.ensure_one()
        _liquidity, counterpart, _writeoff = self._seek_for_lines()
        currency = self.company_id.currency_id
        if len(counterpart) != 1 or currency.is_zero(counterpart.amount_residual):
            raise LockedPaymentError(
                _(
                    "Payment %(payment)s belongs to a closed period and has no "
                    "single open customer line, so its customer cannot be changed. "
                    "Ask accounting.",
                    payment=self.name,
                ),
                "partner",
            )
        transfer = self.env["account.move"]._create_partner_transfer_entry(
            counterpart,
            partner,
            abs(counterpart.amount_residual),
            fields.Date.context_today(self),
            _(
                "%(payment)s transferred to %(partner)s",
                payment=self.name,
                partner=partner.display_name,
            ),
            move_vals=self.move_id._rectification_transfer_vals(),
        )
        self.message_post(
            body=Markup("<p>%s</p>")
            % _(
                "The payment belongs to a closed period, so its customer was not "
                "changed: %(entry)s moves its balance to %(partner)s, who has it in "
                "their favour until an invoice is reconciled with it.",
                entry=transfer.name,
                partner=partner.display_name,
            )
        )
        return transfer

    def _register_locked_payment_difference(self, amount, partner, correction_vals):
        """Register, dated today, a payment or a refund for the difference
        between ``amount`` and the amount of this payment once its corrections
        are counted. Returns it, or nothing when there is no difference."""
        self.ensure_one()
        currency = self.currency_id or self.company_id.currency_id
        difference = amount - self._amended_amount()
        if currency.is_zero(difference):
            return self.browse()
        if difference > 0:
            payment_type = self.payment_type
        else:
            payment_type = "outbound" if self.payment_type == "inbound" else "inbound"
        method_line = self.journal_id._get_available_payment_method_lines(payment_type)[
            :1
        ]
        if not method_line:
            raise LockedPaymentError(
                _(
                    "Journal %(journal)s has no payment method to register the "
                    "difference of %(payment)s. Ask accounting.",
                    journal=self.journal_id.display_name,
                    payment=self.name,
                ),
                "unsupported",
            )
        ref = _("Correction of %s", self.name)
        if self.ref:
            ref = f"{ref} - {self.ref}"
        vals = {
            "payment_type": payment_type,
            "partner_type": self.partner_type,
            "partner_id": partner.id,
            "amount": abs(difference),
            "currency_id": currency.id,
            "date": fields.Date.context_today(self),
            "journal_id": self.journal_id.id,
            "payment_method_line_id": method_line.id,
            # The account of the payment it corrects: without a customer the
            # core would take the first receivable account of the company.
            "destination_account_id": self.destination_account_id.id,
            "ref": ref,
            "amended_payment_id": self.id,
        }
        vals.update(correction_vals)
        correction = self.create(vals)
        correction.action_post()
        self.message_post(
            body=Markup("<p>%s</p>")
            % _(
                "The payment belongs to a closed period, so its amount was not "
                "changed: %(correction)s registers the difference of %(amount)s.",
                correction=correction.name,
                amount=difference,
            )
        )
        return correction
