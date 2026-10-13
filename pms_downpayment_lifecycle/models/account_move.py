import logging
from datetime import date

from markupsafe import Markup

from odoo import _, fields, models
from odoo.exceptions import UserError
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
        """
        self.ensure_one()
        applied = self.invoice_line_ids.folio_line_ids
        lines = self.folio_ids.sale_line_ids.filtered(
            lambda sl: sl.is_downpayment and sl not in applied
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
        except Exception as error:  # noqa: BLE001 - recorded, not swallowed
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
        downpayments = self._get_downpayments_to_transfer()
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
        there is not exactly one (instalment payment terms, for instance):
        with several, which balance to move stops being obvious."""
        self.ensure_one()
        lines = self.line_ids.filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
        )
        return lines if len(lines) == 1 else self.env["account.move.line"]

    def _downpayment_reconciled_amount(self):
        """What was actually collected against this down payment, not what it
        was issued for. Moving the full amount of a down payment that was only
        half paid would credit the new customer money nobody ever paid, and
        leave a phantom debt on the old one."""
        self.ensure_one()
        line = self._receivable_line_for_transfer()
        return sum(line.matched_credit_ids.mapped("amount"))

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
        """Credit note for the down payment, posted in an open period.

        ``amount`` rectifies only part of it -- a partial refund gives back
        part of what was collected, and the rest of the down payment stays
        alive. Fits the SII type 'I', which is by differences.

        ``ref`` says on the credit note itself why it was issued; every caller
        rectifies for a different reason and the hotel reads that field.

        ``cancel=False`` is not negotiable: with ``cancel=True`` the core starts
        by unreconciling every line of the original, which would release the
        payment -- the one thing this whole module exists to avoid.

        The SII refund type goes through the context, as every other caller of
        ``_reverse_moves`` does. ``sii_refund_type`` is a stored computed field
        that can be edited, so the reversal copies the down payment's empty
        value onto the credit note and the compute never runs: without the
        context the AEAT rejects the credit note (TipoRectificativa). The
        context key means nothing when the SII module is not installed.
        """
        self.ensure_one()
        credit_note = self.with_context(sii_refund_type="I")._reverse_moves(
            default_values_list=[
                {
                    "date": date,
                    "invoice_date": date,
                    "ref": ref or _("Transfer of down payment %s", self.name),
                }
            ],
            cancel=False,
        )
        if amount is not None:
            self._scale_credit_note(credit_note, amount)
        credit_note.action_post()
        return credit_note

    def _scale_credit_note(self, credit_note, amount):
        """Bring the credit note down to ``amount``, read as a gross total.

        ``price_unit`` is net of tax, so it is scaled by the ratio rather than
        set to the amount: setting it directly would rectify the amount plus its
        VAT. Writing on the line is enough to resync the tax line -- in 16.0
        ``account.move.line.write`` runs inside ``_sync_dynamic_lines``."""
        self.ensure_one()
        rounding = self.currency_id.rounding
        line = credit_note.invoice_line_ids
        if len(line) != 1 or float_is_zero(
            self.amount_total, precision_rounding=rounding
        ):
            raise UserError(
                _(
                    "%s cannot be partially rectified: it does not have exactly "
                    "one line with an amount.",
                    self.display_name,
                )
            )
        line.write({"price_unit": line.price_unit * amount / self.amount_total})
        # Rounding the net figure can leave the gross a cent short. One
        # correction on the same ratio is exact for a proportional tax.
        delta = amount - credit_note.amount_total
        if not float_is_zero(delta, precision_rounding=rounding):
            line.write(
                {
                    "price_unit": line.price_unit
                    + delta * line.price_unit / credit_note.amount_total
                }
            )
        return credit_note

    def _check_transfer_chronology(self, journal, date):
        """account_invoice_constraint_chronology refuses to post while an older
        draft of the same journal and year exists. Checking first turns an
        opaque core error into something the hotel can act on."""
        if not journal.check_chronology:
            return
        domain = [
            ("journal_id", "=", journal.id),
            ("state", "=", "draft"),
            ("invoice_date", "!=", False),
            ("invoice_date", "<", date),
            ("date", ">=", date.replace(month=1, day=1)),
            ("date", "<=", date.replace(month=12, day=31)),
        ]
        # With a dedicated refund sequence the constraint only looks at other
        # refunds, which is what keeps the pile of draft invoices from blocking
        # every transfer.
        domain.append(
            ("move_type", "=", "out_refund")
            if journal.refund_sequence
            else ("move_type", "!=", "entry")
        )

        blocking = self.env["account.move"].search(domain, limit=3)
        if blocking:
            raise UserError(
                _(
                    "The credit note cannot be posted in journal %(journal)s "
                    "while these older drafts are open: %(drafts)s. Validate or "
                    "cancel them first.",
                    journal=journal.display_name,
                    drafts=", ".join(blocking.mapped("display_name")),
                )
            )

    def _create_transfer_entry(self, downpayment, amount, date):
        """Two lines, on the customer accounts taken from the real journal
        items. Never from the partner's property field: if the down payment was
        issued on another account, reconcile() would refuse with 'Entries are
        not from the same account'."""
        self.ensure_one()
        journal = self.company_id._get_downpayment_transfer_journal()
        if not journal:
            raise UserError(
                _(
                    "No journal configured to post the down payment transfer of "
                    "company %s.",
                    self.company_id.display_name,
                )
            )
        source = downpayment._receivable_line_for_transfer()
        target = self._receivable_line_for_transfer()
        if not source or not target:
            raise UserError(
                _(
                    "Cannot build the transfer entry: %(down)s or %(invoice)s "
                    "does not have exactly one customer line.",
                    down=downpayment.display_name,
                    invoice=self.display_name,
                )
            )
        folios = ", ".join(self.folio_ids.mapped("name"))
        label = _(
            "Folio %(folio)s: down payment %(down)s transferred to %(invoice)s",
            folio=folios,
            down=downpayment.name,
            invoice=self.name,
        )
        # The property is what carries the analytic: pms creates an analytic
        # account per property and an account.analytic.distribution.model keyed
        # on pms_property_id, and account.move.line._compute_analytic_distribution
        # feeds that property into the context. Set the property and the analytic
        # follows by itself; leave it out and this is the one entry in the ledger
        # with neither.
        transfer = self.env["account.move"].create(
            {
                "move_type": "entry",
                "journal_id": journal.id,
                "date": date,
                "ref": label,
                "company_id": self.company_id.id,
                "pms_property_id": self.pms_property_id.id,
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": label,
                            "account_id": source.account_id.id,
                            "partner_id": downpayment.partner_id.id,
                            "debit": amount,
                            "credit": 0.0,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "name": label,
                            "account_id": target.account_id.id,
                            "partner_id": self.partner_id.id,
                            "debit": 0.0,
                            "credit": amount,
                        },
                    ),
                ],
            }
        )
        transfer.action_post()
        return transfer

    # ------------------------------------------------------------------
    # pms_autoinvoice
    # ------------------------------------------------------------------
    def _reverse_downpayment_invoices(self):
        """Keep the down payments of a locked period out of pms_autoinvoice's
        reversal, and leave them to the transfer above.

        pms_autoinvoice reverses the down payments that a final invoice did not
        deduct with ``cancel=True``, which starts by unreconciling them from
        their payment: in a locked period that rewrites the customer balances
        of a month that is already closed. The transfer settles the same down
        payments when the final invoice is posted, rectifying them with
        ``cancel=False`` and moving the balance with a transfer entry.
        """
        locked = self.filtered(lambda move: move._is_in_locked_period())
        return super(AccountMove, self - locked)._reverse_downpayment_invoices()

    def _is_in_locked_period(self):
        """Read from the company, not from ``_get_user_fiscal_lock_date``: the
        cron runs as a user that only sees the fiscal year lock, and the
        closed month is locked through ``period_lock_date``."""
        self.ensure_one()
        lock_date = max(
            self.company_id.period_lock_date or date.min,
            self.company_id.fiscalyear_lock_date or date.min,
        )
        return self.date <= lock_date
