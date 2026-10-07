"""Rectify a customer invoice without breaking what it is reconciled with.

The core way to replace a posted invoice is a credit note with
``_reverse_moves(cancel=True)``: it starts by unreconciling every line of the
original, so the payment that settled it is released and ends up on the
replacement. When that payment belongs to a closed period, the customer
balances of a month that is already closed change.

These pieces do the same job without unreconciling anything: the credit note is
issued with ``cancel=False``, the original keeps its payment, and the money it
collected is moved to the replacement with a transfer entry dated today. It is
what pms_downpayment_lifecycle does with down payments, without the down payment
specifics, so that any invoice edit can use it.

``_reissue_invoice`` only takes that way where unreconciling does harm (see
``_keeps_reconciliation_on_rectify``). An invoice of an open period settled by
payments is still reversed the core way, and its replacement takes over the
same payments.
"""
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    # ------------------------------------------------------------------
    # building blocks
    # ------------------------------------------------------------------
    def _get_single_receivable_line(self):
        """The customer line of this move, or an empty recordset when there is
        not exactly one (instalment payment terms, for instance): with several,
        which balance to move stops being obvious."""
        self.ensure_one()
        lines = self.line_ids.filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
        )
        return lines if len(lines) == 1 else self.env["account.move.line"]

    def _is_rectified(self):
        """Whether posted credit notes issued against this invoice cover all of
        it.

        Not ``payment_state == 'reversed'``: a credit note issued with
        ``cancel=False`` leaves the original reconciled with its payment, so the
        original keeps showing 'paid' once it has been rectified. And a final
        invoice settled only through credit notes and transfer entries may show
        'reversed' without having been rectified at all.
        """
        self.ensure_one()
        rectifications = self.reversal_move_id.filtered(lambda m: m.state == "posted")
        if not rectifications:
            return False
        refunded = sum(rectifications.mapped("amount_total"))
        return self.currency_id.compare_amounts(refunded, self.amount_total) >= 0

    def _check_rectification_chronology(self, journal, date):
        """account_invoice_constraint_chronology refuses to post while an older
        draft of the same journal and year exists. Checking first turns an
        opaque error into something the user can act on. Does nothing when that
        module is not installed."""
        if "check_chronology" not in journal._fields or not journal.check_chronology:
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
        # every credit note.
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

    def _scale_credit_note(self, credit_note, amount):
        """Bring the credit note down to ``amount``, read as a gross total.

        ``price_unit`` is net of tax, so it is scaled by the ratio rather than
        set to the amount: setting it directly would rectify the amount plus its
        VAT. Writing on the line is enough to resync the tax line -- in 16.0
        ``account.move.line.write`` runs inside ``_sync_dynamic_lines``."""
        self.ensure_one()
        line = credit_note.invoice_line_ids
        if len(line) != 1 or self.currency_id.is_zero(self.amount_total):
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
        if not self.currency_id.is_zero(delta):
            line.write(
                {
                    "price_unit": line.price_unit
                    + delta * line.price_unit / credit_note.amount_total
                }
            )
        return credit_note

    def _reconcile_open_lines_with(self, other):
        """Reconcile, account by account, the lines of this move and ``other``
        that are still open. It is what ``_reverse_moves(cancel=True)`` does
        after unreconciling, limited to what nothing else has settled."""
        lines = (self.line_ids | other.line_ids).filtered(
            lambda line: not line.reconciled and line.account_id.reconcile
        )
        for account in lines.account_id:
            group = lines.filtered(lambda line, acc=account: line.account_id == acc)
            if len(group.move_id) == 2:
                group.reconcile()

    def _rectify_keep_reconciliation(self, date, amount=None, ref=None):
        """Credit note for this invoice, posted in an open period, that leaves
        its reconciliations alone.

        ``cancel=False`` is the whole point: with ``cancel=True`` the core
        starts by unreconciling every line of the original, which releases the
        payment. A full credit note also settles whatever the original still
        had open, so what stays open on it is exactly what was collected.

        ``amount`` rectifies only part of it (single line invoices only). That
        credit note is left open: its caller knows what it is for -- the refund
        it gives back, in pms_downpayment_lifecycle -- and settling it against
        the original's open balance would take that place.

        The SII refund type goes through the context, as every other caller of
        ``_reverse_moves`` does: ``sii_refund_type`` is a stored computed field
        that can be edited, so the reversal copies the invoice's empty value and
        the compute never runs. The key means nothing without the SII module.
        """
        self.ensure_one()
        credit_note = self.with_context(sii_refund_type="I")._reverse_moves(
            default_values_list=[
                {
                    "date": date,
                    "invoice_date": date,
                    "ref": ref or _("Rectification of %s", self.name),
                }
            ],
            cancel=False,
        )
        if amount is not None:
            self._scale_credit_note(credit_note, amount)
        credit_note.action_post()
        if amount is None:
            self._reconcile_open_lines_with(credit_note)
        return credit_note

    @api.model
    def _create_balance_transfer_entry(
        self, source_line, target_line, amount, date, label, move_vals=None
    ):
        """Two-line entry that moves ``amount`` from the customer of
        ``source_line`` onto the customer of ``target_line``.

        Accounts and partners are taken from those real journal items, never
        from the partner's properties: reconcile() refuses lines on different
        accounts, and only the partner of the journal item nets the balance per
        customer. The debit goes to the source (it settles a credit there), the
        credit to the target.
        """
        company = source_line.company_id
        journal = company._get_rectification_transfer_journal()
        if not journal:
            raise UserError(
                _(
                    "No journal configured to post the balance transfer entries "
                    "of company %s.",
                    company.display_name,
                )
            )
        vals = {
            "move_type": "entry",
            "journal_id": journal.id,
            "date": date,
            "ref": label,
            "company_id": company.id,
            "line_ids": [
                (
                    0,
                    0,
                    {
                        "name": label,
                        "account_id": source_line.account_id.id,
                        "partner_id": source_line.partner_id.id,
                        "debit": amount,
                        "credit": 0.0,
                    },
                ),
                (
                    0,
                    0,
                    {
                        "name": label,
                        "account_id": target_line.account_id.id,
                        "partner_id": target_line.partner_id.id,
                        "debit": 0.0,
                        "credit": amount,
                    },
                ),
            ],
        }
        vals.update(move_vals or {})
        transfer = self.create(vals)
        transfer.action_post()
        return transfer

    def _rectification_transfer_vals(self):
        """Extra values for the transfer entries this invoice receives. Hook for
        modules that carry more on the entry (a hotel, an analytic)."""
        self.ensure_one()
        return {}

    def _settle_rectification(self, credit_note, date, label=None):
        """Settle what ``credit_note`` still has open against what this invoice
        -- its replacement -- still owes, through a transfer entry.

        Always through the entry, even for the same customer on the same
        account. Reconciled directly with a credit note, the replacement would
        show as 'reversed' -- the core labels so an invoice settled only by
        credit notes -- while it is the live invoice of the stay. And the entry
        leaves the trail of where the money went.

        Only what the replacement still owes is moved. When it bills less, the
        rest stays open on the credit note, in favour of the customer who paid:
        moved, it would end up in favour of the new customer, who paid nothing.
        When it bills more, the customer owes the difference.

        Returns the transfer entry, or an empty recordset when nothing was moved.
        """
        self.ensure_one()
        source = credit_note._get_single_receivable_line()
        target = self._get_single_receivable_line()
        if not source or not target:
            raise UserError(
                _(
                    "%(refund)s or %(invoice)s does not have exactly one customer "
                    "line, so which balance to move is ambiguous.",
                    refund=credit_note.display_name,
                    invoice=self.display_name,
                )
            )
        amount = min(-source.amount_residual, target.amount_residual)
        if self.company_id.currency_id.compare_amounts(amount, 0.0) <= 0:
            return self.browse()
        transfer = self._create_balance_transfer_entry(
            source,
            target,
            amount,
            date,
            label
            or _(
                "%(refund)s transferred to %(invoice)s",
                refund=credit_note.name,
                invoice=self.name,
            ),
            move_vals=self._rectification_transfer_vals(),
        )
        (transfer.line_ids.filtered(lambda line: line.debit) | source).reconcile()
        (transfer.line_ids.filtered(lambda line: line.credit) | target).reconcile()
        return transfer

    # ------------------------------------------------------------------
    # the operation
    # ------------------------------------------------------------------
    def _rectification_counterpart_lines(self):
        """The journal items reconciled with the customer lines of this
        invoice: its payments, and whatever else settled it."""
        self.ensure_one()
        receivables = self.line_ids.filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
        )
        partials = receivables.matched_debit_ids | receivables.matched_credit_ids
        return (partials.debit_move_id | partials.credit_move_id) - receivables

    def _rectification_payment_lines(self):
        """The payment lines reconciled with this invoice: the money the core
        way releases, which the replacement takes over."""
        return self._rectification_counterpart_lines().filtered(
            lambda line: line.move_id.payment_id
        )

    def _keeps_reconciliation_on_rectify(self):
        """Whether replacing this invoice rectifies it without unreconciling
        anything, the money reaching the replacement through a transfer entry.

        Only where unreconciling does harm, and only when the company has a
        journal for the transfer entries:

        - the invoice, or something reconciled with it, belongs to a locked
          period: unreconciling would change the customer balances of a month
          that is already closed;
        - it is a down payment invoice, whose payment stays applied in the
          period the down payment was issued in;
        - it is settled by something other than a payment -- a transfer entry,
          a credit note, a bank statement line. Released, nothing would bring
          it back to the replacement.

        Otherwise -- an open period and nothing but payments -- the core way
        is fine, and ``_reissue_invoice`` hands those same payments over to the
        replacement.
        """
        self.ensure_one()
        counterparts = self._rectification_counterpart_lines()
        if (
            not counterparts
            or not self.company_id._get_rectification_transfer_journal()
        ):
            return False
        if self._is_downpayment():
            return True
        if any(not line.move_id.payment_id for line in counterparts):
            return True
        return any(
            move.date <= move.company_id._get_reconcile_lock_date()
            for move in self | counterparts.move_id
        )

    def _reconcile_released_payment_lines(self, lines):
        """Settle this invoice with ``lines``, payments that the core
        rectification of the invoice it replaces released, as far as they are
        still open. What it does not take stays open on the payment, for the
        next invoice of the customer.

        A payment on another customer account -- the replacement is billed to
        a group company on 433, the payment sat on 430 -- cannot be reconciled
        with it. Its money reaches the replacement through a transfer entry,
        when the company has a journal for them; otherwise it stays open, as
        the core way leaves it.
        """
        self.ensure_one()
        target = self._get_single_receivable_line()
        if not target:
            return
        lines = lines.filtered(lambda line: not line.reconciled)
        same_account = lines.filtered(lambda line: line.account_id == target.account_id)
        if same_account and not target.reconciled:
            (target | same_account).reconcile()
        if not self.company_id._get_rectification_transfer_journal():
            return
        currency = self.company_id.currency_id
        date = fields.Date.context_today(self)
        for line in lines - same_account:
            amount = min(-line.amount_residual, target.amount_residual)
            if currency.compare_amounts(amount, 0.0) <= 0:
                break
            transfer = self._create_balance_transfer_entry(
                line,
                target,
                amount,
                date,
                _(
                    "%(payment)s transferred to %(invoice)s",
                    payment=line.move_id.name,
                    invoice=self.name,
                ),
                move_vals=self._rectification_transfer_vals(),
            )
            (
                transfer.line_ids.filtered(lambda move_line: move_line.debit) | line
            ).reconcile()
            (
                transfer.line_ids.filtered(lambda move_line: move_line.credit) | target
            ).reconcile()

    def _rectify_for_replacement(self, ref=None):
        """Rectify this posted invoice, which a new one is about to replace,
        keeping its reconciliations. Returns the credit note, posted today,
        with what the original collected still open on it: the replacement
        takes it over with ``_settle_rectification`` once it is posted.

        Only for an invoice that ``_keeps_reconciliation_on_rectify()``.
        """
        self.ensure_one()
        if self.reversal_move_id.filtered(lambda m: m.state == "posted"):
            raise UserError(
                _(
                    "%s already has credit notes issued against it, so rectifying "
                    "it again would credit part of it twice. Ask accounting to "
                    "finish the rectification.",
                    self.display_name,
                )
            )
        if not self._get_single_receivable_line():
            raise UserError(
                _(
                    "%s has more than one customer line, so which balance to move "
                    "is ambiguous. Ask accounting to rectify it.",
                    self.display_name,
                )
            )
        date = fields.Date.context_today(self)
        self._check_rectification_chronology(self.journal_id, date)
        return self._rectify_keep_reconciliation(date, ref=ref)

    def _reissue_invoice(self, new_invoice, default_values_list=None):
        """Rectify this posted invoice and post ``new_invoice``, the draft that
        replaces it. Returns the credit note.

        Where unreconciling does no harm (see
        ``_keeps_reconciliation_on_rectify``): the core way, a credit note with
        ``cancel=True``, the original ends 'reversed' and the replacement takes
        over the payments it released. Otherwise nothing is unreconciled: the
        original keeps its payment, the credit note settles what it still had
        open, and the money moves to the replacement with a transfer entry
        dated today.
        """
        self.ensure_one()
        if not self._keeps_reconciliation_on_rectify():
            payments = self._rectification_payment_lines()
            credit_note = self.with_context(sii_refund_type="I")._reverse_moves(
                default_values_list, cancel=True
            )
            # The released payments go to the replacement before anything else
            # of the folio is proposed to it: left to the autoreconciliation,
            # they are only matched when the amounts make it obvious.
            new_invoice.with_context(skip_folio_autoreconcile=True).action_post()
            new_invoice._reconcile_released_payment_lines(payments)
            return credit_note
        ref = (default_values_list or [{}])[0].get("ref")
        credit_note = self._rectify_for_replacement(ref=ref)
        # The replacement is posted without the folio autoreconciliation: the
        # money the original collected has to reach it first, through the
        # credit note, before any other open payment is proposed to it.
        new_invoice.with_context(skip_folio_autoreconcile=True).action_post()
        transfer = new_invoice._settle_rectification(credit_note, credit_note.date)
        self._post_reissue_message(credit_note, new_invoice, transfer)
        return credit_note

    def _post_reissue_message(self, credit_note, new_invoice, transfer):
        """Say on both invoices where the money of this one went."""
        self.ensure_one()
        body = (
            _(
                "Rectified with %(refund)s and replaced by %(invoice)s, keeping "
                "its payments. The balance was moved with %(entry)s.",
                refund=credit_note.name,
                invoice=new_invoice.name,
                entry=transfer.name,
            )
            if transfer
            else _(
                "Rectified with %(refund)s and replaced by %(invoice)s, keeping "
                "its payments.",
                refund=credit_note.name,
                invoice=new_invoice.name,
            )
        )
        for move in self | new_invoice:
            move.message_post(body=Markup("<p>%s</p>") % body)
