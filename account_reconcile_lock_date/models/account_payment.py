from markupsafe import Markup

from odoo import _, fields, models
from odoo.exceptions import UserError

from .account_move import BYPASS_CONTEXT_KEY, BYPASS_GROUP


class LockedPaymentError(UserError):
    """A change that a payment of a locked period cannot take. ``code`` says
    which one (``journal``, ``date``, ``partner``, ``amount``, ``bank``,
    ``downpayment``, ``unsupported``), so that an API can answer it with a
    typed problem."""

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
        help="The payment of a locked period this one corrected. Corrections "
        "are no longer registered: a payment of an open period that settles a "
        "document of a closed one is changed itself, and one of a closed period "
        "is left to accounting. The ones registered before still count in the "
        "amount of the payment they correct.",
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
        """Apply ``vals`` to this payment of a locked period. Nothing is
        registered beside it: the change is made to the payment itself, or
        refused for accounting to correct.

        - ``ref`` is written in place: it is no accounting field.
        - A payment dated within the closed period takes nothing else, except a
          new customer when it settles nothing: a transfer entry dated today
          moves its open balance to them.
        - A payment of an open period, locked only by what it settles (an
          invoice of the closed period), is changed like any other payment: it
          is unreconciled, changed and reconciled again with the same
          documents, the oldest first. The reconciliation is dated in the open
          period, so the closed one is left as it was. Its customer follows the
          invoice it settles and cannot change; its date moves in place.
        - ``journal_id``, and anything else, is refused.

        ``amount`` is what the payment adds up to with the corrections
        registered before this rule, which is the figure the channels compare.

        ``correction_vals`` is no longer used: kept for the callers. Returns an
        empty recordset.
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
        currency = self.currency_id or self.company_id.currency_id
        amount = None
        if "amount" in vals:
            amount = currency.round(
                vals.pop("amount") - (self._amended_amount() - self.amount)
            )
            if currency.compare_amounts(amount, self.amount) == 0:
                amount = None
        partner = self.partner_id
        if "partner_id" in vals:
            partner = self.env["res.partner"].browse(vals.pop("partner_id"))
        # Everything is checked before anything is done.
        if amount is not None:
            self._check_locked_amount_change(amount)
        if partner != self.partner_id:
            self._check_locked_partner_change()
        if "date" in vals:
            self._move_locked_payment_date(vals.pop("date"))
        if partner != self.partner_id:
            self._transfer_locked_payment_partner(partner)
        if amount is not None:
            self._change_locked_payment_amount(amount)
        if "ref" in vals:
            self.move_id.ref = vals.pop("ref")
        return self.browse()

    def _settled_counterpart_lines(self):
        """The journal items reconciled with the customer or supplier lines of
        this payment: what it settles."""
        self.ensure_one()
        lines = self.move_id.line_ids.filtered(
            lambda line: line.account_id.account_type
            in ("asset_receivable", "liability_payable")
        )
        partials = lines.matched_debit_ids | lines.matched_credit_ids
        return (partials.debit_move_id | partials.credit_move_id) - lines

    def _check_locked_amount_change(self, amount):
        """Refuse a new amount that a payment of a locked period cannot take.

        Only a payment of an open period can change, and only when changing it
        leaves the documents it settles as they would be after any other
        payment edit: not one reconciled with the bank (the reconciliation of
        the statement would break), not one that settles an invoice already
        rectified (the difference would end up on the invoice that no longer
        stands), not one that settles a down payment invoice (the down payment
        has its own way to follow a new amount)."""
        self.ensure_one()
        lock_date = self.company_id._get_reconcile_lock_date()
        currency = self.currency_id or self.company_id.currency_id
        if self.date <= lock_date:
            raise LockedPaymentError(
                _(
                    "Payment %(payment)s is dated within a closed period (locked "
                    "up to %(lock)s): its amount cannot be changed. Ask accounting "
                    "to correct it.",
                    payment=self.name,
                    lock=lock_date,
                ),
                "amount",
            )
        if currency.compare_amounts(amount, 0.0) <= 0:
            raise LockedPaymentError(
                _(
                    "Payment %(payment)s cannot take an amount of %(amount)s. "
                    "Register a refund instead.",
                    payment=self.name,
                    amount=amount,
                ),
                "amount",
            )
        if self.is_matched:
            raise LockedPaymentError(
                _(
                    "Payment %s is reconciled with a bank statement: its amount "
                    "cannot be changed. Ask accounting to correct it.",
                    self.name,
                ),
                "bank",
            )
        self._check_settles_no_rectified_invoice()
        downpayments = self._settled_counterpart_lines().move_id.filtered(
            lambda move: move._is_downpayment()
        )
        if downpayments:
            raise LockedPaymentError(
                _(
                    "Payment %(payment)s settles the down payment %(down)s: its "
                    "amount cannot be changed this way. Ask accounting to correct "
                    "it.",
                    payment=self.name,
                    down=", ".join(downpayments.mapped("display_name")),
                ),
                "downpayment",
            )

    def _change_locked_payment_amount(self, amount):
        """Change the amount of a payment of an open period that settles a
        document of a closed one: unreconcile it, reset it, change it, post it
        and reconcile it again with the same documents, the oldest first. What
        it no longer covers stays open on the newest of them; what it covers
        beyond them stays open on the payment."""
        self.ensure_one()
        settled = self._settled_counterpart_lines()
        old_amount = self.amount
        # The documents of the closed period are only unreconciled here, from a
        # payment of the open one, and reconciled again in the same operation.
        payment = self.with_context(**{BYPASS_CONTEXT_KEY: True})
        payment.action_draft()
        payment.write({"amount": amount})
        payment.action_post()
        own = self.move_id.line_ids.filtered(
            lambda line: line.account_id.account_type
            in ("asset_receivable", "liability_payable")
            and not line.reconciled
        )
        # One document at a time, the oldest first: given them all at once,
        # the core does not settle them in that order.
        pending = settled.filtered(lambda line: not line.reconciled).sorted(
            lambda line: (line.date_maturity or line.date, line.id)
        )
        for line in pending:
            mine = own.filtered(
                lambda own_line, acc=line.account_id: own_line.account_id == acc
                and not own_line.reconciled
            )
            if mine and not line.reconciled:
                (mine | line).reconcile()
        self.message_post(
            body=Markup("<p>%s</p>")
            % _(
                "Amount changed from %(old)s to %(new)s. The payment settles "
                "%(documents)s, of a closed period: it was unreconciled and "
                "reconciled again with them in the open period.",
                old=old_amount,
                new=amount,
                documents=", ".join(settled.move_id.mapped("display_name")),
            )
        )

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
