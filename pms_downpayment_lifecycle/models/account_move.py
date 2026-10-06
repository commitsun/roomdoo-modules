import logging

from markupsafe import Markup
from psycopg2 import OperationalError

from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.service.model import PG_CONCURRENCY_ERRORS_TO_RETRY
from odoo.tools import float_compare, float_is_zero

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = "account.move"

    downpayment_transfer_state = fields.Selection(
        selection=[
            ("pending", "Pending"),
            ("done", "Done"),
            ("manual", "Manual review"),
            ("failed", "Failed"),
        ],
        string="Down payment transfer",
        copy=False,
        readonly=True,
        index="btree_not_null",
        help="Set on a final invoice that absorbed a down payment issued to a "
        "different customer. The transfer itself runs in its own job, so that a "
        "down payment that will not settle can never stop the stay from being "
        "invoiced.",
    )
    downpayment_transfer_message = fields.Text(
        string="Down payment transfer detail",
        copy=False,
        readonly=True,
    )

    # ------------------------------------------------------------------
    # detection
    # ------------------------------------------------------------------
    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        # Only marks and enqueues. The accounting work is deliberately kept out
        # of _post(): pms_autoreconcile_folio_payments also overrides it and
        # reconciles the very lines this would touch, and above all the duty to
        # invoice the guest cannot depend on an old down payment settling.
        posted._mark_downpayment_transfer()
        return posted

    def _mark_downpayment_transfer(self):
        for move in self:
            if move.move_type != "out_invoice" or not move.folio_ids:
                continue
            if move._is_downpayment():
                continue
            if not move._get_downpayments_to_transfer():
                continue
            move.downpayment_transfer_state = "pending"
            move.with_delay()._transfer_downpayments()

    def _get_downpayments_to_transfer(self):
        """Down payment invoices of this invoice's folios that this invoice did
        not take into account, and that still have something standing.

        pms applies a down payment by adding a line linked to its
        folio.sale.line, and skips the ones issued to another customer with a
        bare `continue` (pms_folio.py, the down payment section). So "not
        applied here" is read off the invoice itself, and it covers the two
        cases that need us -- the down payment left dangling on another
        customer, and the one a draft made before it never discounted -- while
        leaving alone the one this invoice already discounted. A down payment
        that is already applied is settled business.

        Deliberately not keyed on `qty_invoiced`: a partial credit note takes a
        whole unit off it, so a down payment of 100 with 40 refunded drops to
        zero and would disappear from here with 60 still standing on the
        customer's account.

        Nor one that another invoice of the folio already discounts. A folio
        billed in several invoices -- rooms checking out on different days --
        has the down payment deducted in the first one, and the later ones do
        not carry the line: transferring it to them would deduct it twice.
        """
        self.ensure_one()
        applied = self.invoice_line_ids.folio_line_ids
        lines = self.folio_ids.sale_line_ids.filtered(
            lambda sl: sl.is_downpayment
            and sl not in applied
            and not self._downpayment_deducted_elsewhere(sl)
        )
        rounding = self.company_id.currency_id.rounding
        return (
            lines.invoice_lines.mapped("move_id")
            .filtered(
                lambda m: (
                    m.state == "posted"
                    and m.move_type == "out_invoice"
                    and m.company_id == self.company_id
                    and m._is_downpayment()
                    # Never a document this module itself produced: an
                    # out_invoice that reverses a credit note is a counter-invoice,
                    # not a down payment, however much it looks like one.
                    and not m.reversed_entry_id
                    and float_compare(
                        m._amount_open_to_rectify(), 0.0, precision_rounding=rounding
                    )
                    > 0
                )
            )
            .sorted(lambda m: (m.invoice_date or m.date, m.id))
        )

    def _downpayment_deducted_elsewhere(self, sale_line):
        """Whether another invoice that still stands discounts the down payment
        line ``sale_line``: a final invoice, posted or still in draft, with the
        line at a negative quantity. A rectified one no longer counts."""
        self.ensure_one()
        return any(
            line.quantity < 0
            and line.move_id != self
            and line.move_id.move_type == "out_invoice"
            and line.move_id.state != "cancel"
            and not line.move_id._is_downpayment()
            and not line.move_id._is_rectified()
            for line in sale_line.invoice_lines
        )

    def _downpayment_association_ambiguity(self, downpayments):
        """Why this invoice cannot be said to be the one a down payment belongs
        to, or False when it plainly is.

        Associating money by guesswork is worse than asking: when more than one
        reading is possible the case is handed over with the reason written on
        it, and nothing is posted.
        """
        self.ensure_one()
        rounding = self.company_id.currency_id.rounding
        standing = sum(d._amount_open_to_rectify() for d in downpayments)
        if float_compare(standing, self.amount_total, precision_rounding=rounding) > 0:
            return _(
                "%(standing)s is standing as down payments on this folio while "
                "this invoice only bills %(billed)s, so which part of it belongs "
                "here cannot be told apart.",
                standing=standing,
                billed=self.amount_total,
            )
        pending = {
            line_id: qty
            for line_id, qty in (self.folio_ids._get_lines_to_invoice() or {}).items()
            if qty > 0
        }
        if pending:
            return _(
                "The folio still has %s line(s) left to invoice, so it cannot be "
                "told whether the down payment belongs to this invoice or to the "
                "next one.",
                len(pending),
            )
        return False

    # ------------------------------------------------------------------
    # the transfer itself (queue job)
    # ------------------------------------------------------------------
    def _transfer_downpayments(self):
        """Rectify each down payment this invoice did not apply and put its
        balance where it belongs, without touching the period it was issued in.

        Whatever goes wrong, the invoice is left saying so. A job that dies in
        the queue and leaves the invoice on 'pending' with an empty message
        gives the hotel nothing to act on, which is the same as losing it.
        """
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                return self._run_downpayment_transfer()
        except OperationalError as error:
            # A lock or a serialization conflict with another posting -- every
            # entry of the company takes the same entry sequence -- is not a
            # failure of the transfer. Raised as it is, queue_job's runner
            # retries it with no limit; turned into a RetryableJobError here it
            # would count against max_retries, and after the fifth the job
            # would fail leaving the invoice 'pending' with no message.
            if error.pgcode in PG_CONCURRENCY_ERRORS_TO_RETRY:
                raise
            return self._record_downpayment_transfer_failure(error)
        except Exception as error:  # noqa: BLE001 - recorded, not swallowed
            return self._record_downpayment_transfer_failure(error)

    def _record_downpayment_transfer_failure(self, error):
        message = str(error)
        self.downpayment_transfer_state = "failed"
        self.downpayment_transfer_message = message
        self.message_post(
            body=Markup("<p>%s</p><p>%s</p>")
            % (_("Down payment transfer failed"), message)
        )
        _logger.exception("Down payment transfer failed for %s", self.display_name)
        return False

    def _run_downpayment_transfer(self):
        self.ensure_one()
        if self._is_rectified():
            # Rectified while the job waited, typically edited from the app
            # right after posting. Transferring to it would leave the money
            # open on a document that no longer stands: the invoice that
            # replaces it was posted with a transfer of its own.
            message = _(
                "Nothing transferred: this invoice was rectified before the "
                "transfer ran. The invoice that replaces it, if any, takes the "
                "down payments when it is posted."
            )
            self.downpayment_transfer_state = "done"
            self.downpayment_transfer_message = message
            self.message_post(body=Markup("<p>%s</p>") % message)
            return True
        downpayments = self._get_downpayments_to_transfer()
        if not downpayments:
            # Marked at posting, rectified by something else before the job
            # ran. Saying so is what tells this apart from a transfer that was
            # done.
            message = _(
                "Nothing left to transfer: when the transfer ran, the down "
                "payments of this folio had already been rectified."
            )
            self.downpayment_transfer_state = "done"
            self.downpayment_transfer_message = message
            self.message_post(body=Markup("<p>%s</p>") % message)
            return True
        ambiguity = self._downpayment_association_ambiguity(downpayments)
        if ambiguity:
            self.downpayment_transfer_state = "manual"
            self.downpayment_transfer_message = ambiguity
            self.message_post(
                body=Markup("<p>%s</p><p>%s</p>")
                % (_("Down payment left for review"), ambiguity)
            )
            return True
        notes = []
        for downpayment in downpayments:
            notes.append(self._transfer_one_downpayment(downpayment))
        state = "manual" if any(n[0] == "manual" for n in notes) else "done"
        self.downpayment_transfer_state = state
        self.downpayment_transfer_message = "\n".join(n[1] for n in notes)
        self.message_post(
            body=Markup("<p>%s</p><ul>%s</ul>")
            % (
                _("Down payment transfer"),
                Markup("").join(Markup("<li>%s</li>") % n[1] for n in notes),
            )
        )
        return True

    def _transfer_one_downpayment(self, downpayment):
        """Returns (outcome, message). Anything that needs a human returns
        'manual' instead of raising: one awkward down payment must not send the
        whole job to the failed queue and hide the others."""
        self.ensure_one()
        if downpayment.partner_id == self.partner_id:
            return self._apply_one_downpayment(downpayment)
        anonymous = self.env.ref("pms.various_pms_partner", raise_if_not_found=False)
        if not anonymous or downpayment.partner_id != anonymous:
            return (
                "manual",
                _(
                    "%(name)s is invoiced to %(partner)s, an identified third "
                    "party, so it is left for accounting: rectifying VAT charged "
                    "to a real customer without refunding them is not the same "
                    "operation as exchanging a simplified invoice.",
                    name=downpayment.name,
                    partner=downpayment.partner_id.display_name,
                ),
            )

        source = downpayment._receivable_line_for_transfer()
        target = self._receivable_line_for_transfer()
        if not source or not target:
            return (
                "manual",
                _(
                    "%(name)s or this invoice does not have exactly one customer "
                    "line, so which balance to move is ambiguous.",
                    name=downpayment.name,
                ),
            )

        rounding = self.company_id.currency_id.rounding
        # Never more than what is left to rectify, and never more than what was
        # collected. A down payment that was partly refunded already carries a
        # credit note for that part and the money went back to the guest, so
        # moving everything it ever collected would credit the new customer a
        # balance that is no longer there.
        amount = min(
            downpayment._amount_open_to_rectify(),
            downpayment._downpayment_reconciled_amount(),
        )
        if float_is_zero(amount, precision_rounding=rounding):
            return (
                "manual",
                _(
                    "%(name)s has nothing left standing against it -- either "
                    "nothing was collected, or what was collected has already "
                    "been given back -- so there is no balance to move.",
                    name=downpayment.name,
                ),
            )

        if not self.company_id._get_rectification_transfer_journal():
            return (
                "manual",
                _(
                    "%(name)s was not transferred: %(company)s has no down "
                    "payment transfer journal configured.",
                    name=downpayment.name,
                    company=self.company_id.display_name,
                ),
            )
        date = fields.Date.context_today(self)
        self._check_transfer_chronology(downpayment.journal_id, date)
        credit_note = downpayment._rectify_downpayment(
            date, amount=downpayment._partial_rectification_amount()
        )
        transfer = self._create_transfer_entry(downpayment, amount, date)

        # A back to zero: the transfer's debit against the credit note.
        (
            transfer.line_ids.filtered(lambda line: line.debit)
            | credit_note._receivable_line_for_transfer()
        ).reconcile()

        # B: apply it to the final invoice, as far as it still owes.
        pending = target.filtered(lambda line: not line.reconciled)
        if pending:
            (transfer.line_ids.filtered(lambda line: line.credit) | pending).reconcile()
            return (
                "done",
                _(
                    "%(name)s rectified as %(refund)s and transferred with %(entry)s.",
                    name=downpayment.name,
                    refund=credit_note.name,
                    entry=transfer.name,
                ),
            )
        return (
            "done",
            _(
                "%(name)s rectified as %(refund)s and transferred with "
                "%(entry)s. The final invoice was already settled, so the amount "
                "is left open in favour of the customer.",
                name=downpayment.name,
                refund=credit_note.name,
                entry=transfer.name,
            ),
        )

    def _apply_one_downpayment(self, downpayment):
        """Same customer on both documents: there is no balance to move between
        accounts, only a document to issue.

        This is the invoice that was drafted before the down payment existed --
        it bills the stay in full and never discounted it. Rectifying the down
        payment and settling its credit note against this invoice leaves the
        customer owing exactly the difference, which is what the discount inside
        the invoice would have achieved, but with its own document.
        """
        self.ensure_one()
        date = fields.Date.context_today(self)
        self._check_transfer_chronology(downpayment.journal_id, date)
        credit_note = downpayment._rectify_downpayment(
            date,
            amount=downpayment._partial_rectification_amount(),
            ref=_("Applied to invoice %s", self.name),
        )
        pending = (credit_note.line_ids | self.line_ids).filtered(
            lambda line: (
                line.account_id.account_type == "asset_receivable"
                and not line.reconciled
            )
        )
        for account in pending.account_id:
            pending.filtered(
                lambda line, acc=account: line.account_id == acc
            ).reconcile()
        return (
            "done",
            _(
                "%(name)s rectified as %(refund)s and applied to this invoice.",
                name=downpayment.name,
                refund=credit_note.name,
            ),
        )

    # ------------------------------------------------------------------
    # building blocks
    # ------------------------------------------------------------------
    def _receivable_line_for_transfer(self):
        """The single customer line of this move, or an empty recordset when
        there is not exactly one. Kept under its old name for the callers of
        this module; the logic lives in account_reconcile_lock_date."""
        return self._get_single_receivable_line()

    def _downpayment_reconciled_amount(self):
        """What was actually collected against this down payment, not what it
        was issued for. Moving the full amount of a down payment that was only
        half paid would credit the new customer money nobody ever paid, and
        leave a phantom debt on the old one."""
        self.ensure_one()
        line = self._receivable_line_for_transfer()
        # Its own credit notes are not money: a full rectification is matched
        # against what the down payment still owed.
        return sum(
            line.matched_credit_ids.filtered(
                lambda partial: partial.credit_move_id.move_id
                not in self.reversal_move_id
            ).mapped("amount")
        )

    def _amount_open_to_rectify(self):
        """What of this down payment has not been rectified yet.

        Measured on the invoiced total, not on what was collected: a credit note
        is a fiscal document and it rectifies what was billed. Anything already
        reversed -- by a transfer, by an earlier partial refund -- is gone from
        the figure, which is what keeps two refunds from rectifying the same
        euro twice."""
        self.ensure_one()
        rectified = sum(
            self.reversal_move_id.filtered(lambda m: m.state == "posted").mapped(
                "amount_total"
            )
        )
        return self.amount_total - rectified

    def _partial_rectification_amount(self):
        """What to pass as ``amount`` to ``_rectify_downpayment``: the part of
        this down payment still to rectify, or None when that is all of it.

        None is not the same as the full figure. It leaves the credit note an
        exact reversal of the original, which is what a full rectification has
        to be, while a number goes through ``_scale_credit_note`` and its
        rounding correction. Only the partial case needs that.
        """
        self.ensure_one()
        rounding = self.company_id.currency_id.rounding
        open_to_rectify = self._amount_open_to_rectify()
        if (
            float_compare(
                open_to_rectify, self.amount_total, precision_rounding=rounding
            )
            < 0
        ):
            return open_to_rectify
        return None

    def _rectify_downpayment(self, date, amount=None, ref=None):
        """Credit note for the down payment, posted in an open period, issued
        with ``cancel=False`` so that the payment stays applied to the down
        payment in the period it was issued in -- the one thing this whole
        module exists to keep.

        ``amount`` rectifies only part of it -- a partial refund gives back
        part of what was collected, and the rest of the down payment stays
        alive. Fits the SII type 'I', which is by differences.

        ``ref`` says on the credit note itself why it was issued; every caller
        rectifies for a different reason and the hotel reads that field.
        """
        self.ensure_one()
        return self._rectify_keep_reconciliation(
            date,
            amount=amount,
            ref=ref or _("Transfer of down payment %s", self.name),
        )

    def _check_transfer_chronology(self, journal, date):
        """Kept under its old name for the callers of this module; the logic
        lives in account_reconcile_lock_date."""
        return self._check_rectification_chronology(journal, date)

    def _create_transfer_entry(self, downpayment, amount, date):
        """The entry that moves a down payment balance onto this invoice's
        customer, built by account_reconcile_lock_date on the accounts and the
        partners of the real journal items."""
        self.ensure_one()
        source = downpayment._get_single_receivable_line()
        target = self._get_single_receivable_line()
        if not source or not target:
            raise UserError(
                _(
                    "Cannot build the transfer entry: %(down)s or %(invoice)s "
                    "does not have exactly one customer line.",
                    down=downpayment.display_name,
                    invoice=self.display_name,
                )
            )
        label = _(
            "Folio %(folio)s: down payment %(down)s transferred to %(invoice)s",
            folio=", ".join(self.folio_ids.mapped("name")),
            down=downpayment.name,
            invoice=self.name,
        )
        return self._create_balance_transfer_entry(
            source,
            target,
            amount,
            date,
            label,
            move_vals=self._rectification_transfer_vals(),
        )

    def _rectification_transfer_vals(self):
        """The property is what carries the analytic: pms creates an analytic
        account per property and an account.analytic.distribution.model keyed
        on pms_property_id, and account.move.line._compute_analytic_distribution
        feeds that property into the context. Set the property and the analytic
        follows by itself; leave it out and the transfer is the one entry in the
        ledger with neither."""
        vals = super()._rectification_transfer_vals()
        vals["pms_property_id"] = self.pms_property_id.id
        return vals

    # ------------------------------------------------------------------
    # pms_autoinvoice
    # ------------------------------------------------------------------
    def _reverse_downpayment_invoices(self):
        """Keep out of pms_autoinvoice's reversal the down payments the transfer
        above settles, and the ones of a locked period.

        pms_autoinvoice reverses the down payments that a final invoice did not
        deduct with ``cancel=True``, which starts by unreconciling them from
        their payment. The payment is then left loose on the down payment's
        customer, and the final invoice shows the stay unpaid: its folio
        autoreconciliation ran when it was posted, before the reversal. The
        transfer, queued when the final invoice was posted, moves the money of
        an anonymous down payment to the final invoice instead, so those are
        left to it whenever they collected money and the company has a transfer
        journal. In a locked period the reversal would also rewrite the customer
        balances of a month that is already closed, so none is reversed there.

        The rest is reversed the usual way: a down payment that collected
        nothing, and one issued to a named customer in an open period, which
        the transfer would only leave for accounting.
        """
        anonymous = self.env.ref("pms.various_pms_partner", raise_if_not_found=False)
        to_transfer = self.filtered(
            lambda move: move._is_in_locked_period()
            or (
                anonymous
                and move.partner_id == anonymous
                and move._keeps_reconciliation_on_rectify()
            )
        )
        return super(AccountMove, self - to_transfer)._reverse_downpayment_invoices()

    def _is_in_locked_period(self):
        """The company's own lock date, not ``_get_user_fiscal_lock_date``: the
        cron runs as a user that only sees the fiscal year lock, and the closed
        month is locked through ``period_lock_date``."""
        self.ensure_one()
        return self.date <= self.company_id._get_reconcile_lock_date()
