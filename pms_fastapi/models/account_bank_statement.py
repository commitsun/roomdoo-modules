from odoo import _, fields, models
from odoo.exceptions import UserError

# Maps account.payment.pms_api_transaction_type (defined in pms_api_rest) to the
# cash-session breakdown buckets. ``internal_transfer`` is handled apart because
# its sign depends on the payment leg direction on this journal.
_INCOME_TYPES = ("customer_inbound", "supplier_inbound")


class AccountBankStatement(models.Model):
    _inherit = "account.bank.statement"

    # A cash session (turno de caja) is backed by an account.bank.statement.
    # The accounting model has no open/closed state, so we track it explicitly
    # instead of inferring it from balances.
    cash_session_closed = fields.Boolean(
        string="Cash session closed",
        default=False,
        copy=False,
        index=True,
    )
    cash_session_closed_uid = fields.Many2one(
        comodel_name="res.users",
        string="Cash session closed by",
        copy=False,
        readonly=True,
    )
    cash_session_closed_date = fields.Datetime(
        string="Cash session closed on",
        copy=False,
        readonly=True,
    )
    cash_session_note = fields.Text(
        string="Cash session note",
        help="Note left for the next shift when closing the cash session.",
        copy=False,
    )

    # -- Cash session breakdown / close (ported from pms_api_rest
    #    pms.transaction.service: _action_close_cash_session,
    #    _session_create_statement_lines, _post_statement_difference) --

    def _pms_cash_session_breakdown(self):
        """Return the shift financial breakdown computed from its payments.

        The window is [opened_at, closed_at] (or now while open). ``expected``
        reproduces the legacy ``balance_start + inbound - outbound`` cash effect
        of this journal, expressed through the contract buckets.
        """
        self.ensure_one()
        income = refund = expense = internal_transfer = 0.0
        for payment in self._pms_cash_session_payments():
            amount = abs(payment.amount)
            ttype = payment.pms_api_transaction_type
            if ttype in _INCOME_TYPES:
                income += amount
            elif ttype == "customer_outbound":
                refund += amount
            elif ttype == "supplier_outbound":
                expense += amount
            elif ttype == "internal_transfer":
                # Net money leaving the cash register through transfers: an
                # outbound leg lowers cash, an inbound leg raises it.
                internal_transfer += (
                    amount if payment.payment_type == "outbound" else -amount
                )
        expected = self.balance_start + income - refund - expense - internal_transfer
        return {
            "income": income,
            "refund": refund,
            "expense": expense,
            "internal_transfer": internal_transfer,
            "expected": expected,
        }

    def _pms_cash_session_payments(self):
        """Posted payments of this journal within the session window."""
        self.ensure_one()
        domain = [
            ("journal_id", "=", self.journal_id.id),
            ("state", "=", "posted"),
            ("create_date", ">=", self.create_date),
        ]
        if self.cash_session_closed and self.cash_session_closed_date:
            domain.append(("create_date", "<=", self.cash_session_closed_date))
        return self.env["account.payment"].sudo().search(domain)

    def _pms_close_cash_session(self, counted_cash, note):
        """Close the cash session recording the physically counted cash.

        Posts the difference as a profit/loss line, creates and reconciles the
        statement lines for the shift payments, and stamps the closing
        metadata. The mismatch never blocks the close.
        """
        self.ensure_one()
        breakdown = self._pms_cash_session_breakdown()
        difference = round(counted_cash - breakdown["expected"], 2)
        session_payments = self._pms_cash_session_payments()
        if difference:
            self._pms_post_statement_difference(difference)
        self._pms_create_statement_lines(session_payments, counted_cash)
        self.write(
            {
                "balance_end_real": counted_cash,
                "cash_session_closed": True,
                "cash_session_closed_uid": self.env.user.id,
                "cash_session_closed_date": fields.Datetime.now(),
                "cash_session_note": note or "",
            }
        )
        return difference

    def _pms_post_statement_difference(self, amount):
        """Create the profit/loss statement line for the cash mismatch."""
        self.ensure_one()
        journal = self.journal_id
        st_line_vals = {
            "statement_id": self.id,
            "journal_id": journal.id,
            "amount": amount,
            "date": fields.Date.today(),
        }
        if amount < 0.0:
            if not journal.loss_account_id:
                raise UserError(
                    _(
                        "Please go on the %s journal and define a Loss Account."
                        " This account will be used to record cash difference.",
                        journal.name,
                    )
                )
            st_line_vals["payment_ref"] = _(
                "Cash difference observed during the counting (Loss) - closing"
            )
            st_line_vals["counterpart_account_id"] = journal.loss_account_id.id
        else:
            if not journal.profit_account_id:
                raise UserError(
                    _(
                        "Please go on the %s journal and define a Profit Account."
                        " This account will be used to record cash difference.",
                        journal.name,
                    )
                )
            st_line_vals["payment_ref"] = _(
                "Cash difference observed during the counting (Profit) - closing"
            )
            st_line_vals["counterpart_account_id"] = journal.profit_account_id.id
        self.env["account.bank.statement.line"].sudo().create(st_line_vals)

    def _pms_create_statement_lines(self, session_payments, counted_cash):
        """Create statement lines for the shift payments and reconcile them."""
        self.ensure_one()
        journal = self.journal_id
        payment_statement_line_match = []
        for payment in session_payments:
            statement_line = (
                self.env["account.bank.statement.line"]
                .sudo()
                .create(
                    {
                        "date": payment.date,
                        "journal_id": journal.id,
                        "amount": payment.amount
                        if payment.payment_type == "inbound"
                        else -payment.amount,
                        "payment_ref": payment.ref,
                        "partner_id": payment.partner_id.id,
                        "pms_property_id": payment.pms_property_id.id,
                        "statement_id": self.id,
                    }
                )
            )
            payment_statement_line_match.append((payment, statement_line))

        # Do not call button post (avoids creating an extra profit/loss line via
        # _check_balance_end_real_same_as_computed).
        if not self.name:
            self._set_next_sequence()
        self.balance_end = counted_cash
        lines_of_moves_to_post = self.line_ids.filtered(
            lambda line: line.move_id.state != "posted"
        )
        if lines_of_moves_to_post:
            lines_of_moves_to_post.move_id._post(soft=False)

        for payment, statement_line in payment_statement_line_match:
            payment_move_line = payment.move_id.line_ids.filtered(
                lambda x, p=payment: x.reconciled is False
                and x.journal_id == journal
                and x.account_id in p._get_valid_liquidity_accounts()
            )
            statement_move_line = statement_line.move_id.line_ids.filtered(
                lambda line: line.account_id.reconcile
                or line.account_id == line.journal_id.suspense_account_id
            )
            if payment_move_line and statement_move_line:
                statement_move_line.account_id = payment_move_line.account_id
                (payment_move_line + statement_move_line).reconcile()
