from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.tools import float_compare, float_is_zero


class AccountPayment(models.Model):
    _inherit = "account.payment"

    downpayment_reissue_partner_id = fields.Many2one(
        string="Down payment to re-issue to",
        comodel_name="res.partner",
        copy=False,
        readonly=True,
        help="Set while a payment sits in draft with its down payment already "
        "rectified: the customer the replacement must be issued to once the new "
        "amount is known.",
    )

    downpayment_refund_state = fields.Selection(
        selection=[
            ("pending", "Pending"),
            ("done", "Done"),
            ("manual", "Manual review"),
            ("failed", "Failed"),
        ],
        string="Down payment refund",
        copy=False,
        readonly=True,
        index="btree_not_null",
        help="Set on a refund that gives back money collected as a down "
        "payment: it rectifies the down payment invoice itself instead of "
        "producing an unrelated credit note.",
    )
    downpayment_refund_message = fields.Text(
        string="Down payment refund detail",
        copy=False,
        readonly=True,
    )

    @api.model
    def _check_has_downpayment_invoice(self, payment):
        """pms asks this, and only this, before destroying the down payment
        invoice of a payment that goes back to draft: it either reverses it with
        ``cancel=True`` -- which unreconciles the payment and can rewrite a
        closed period -- or unlinks it outright, and in neither case does it
        issue a replacement. The folio is then short of an invoice nobody
        notices is missing.

        This module takes that over in ``action_draft``, so the answer is always
        no. Nothing else in pms consults this method.
        """
        return False

    def _downpayment_invoices(self):
        """Posted down payment invoices settled by this payment."""
        self.ensure_one()
        if self.partner_type != "customer":
            return self.env["account.move"]
        return self.reconciled_invoice_ids.filtered(
            lambda inv: inv.state == "posted" and inv._is_downpayment()
        )

    def action_draft(self):
        # Read before calling super(): it unreconciles, and reconciled_invoice_ids
        # is exactly what it empties.
        to_rectify = {
            payment.id: payment._downpayment_invoices()
            for payment in self
            if payment._downpayment_invoices()
        }
        # super() first on purpose. It is the step that can legitimately refuse
        # -- a locked period rejects the unreconcile -- and refusing after having
        # posted a credit note would leave that credit note orphaned.
        res = super().action_draft()
        for payment in self.filtered(lambda p: p.id in to_rectify):
            payment._rectify_downpayments_for_reissue(to_rectify[payment.id])
        return res

    def _rectify_downpayments_for_reissue(self, downpayments):
        """Void the down payments this payment used to settle, and remember who
        they were issued to so the replacement goes to the same customer."""
        self.ensure_one()
        date = fields.Date.context_today(self)
        for downpayment in downpayments:
            credit_note = downpayment._rectify_downpayment(
                date,
                ref=_("Down payment voided: payment %s reset to draft", self.name),
            )
            # Both sides are open now -- super() released the payment -- so the
            # invoice and its credit note settle each other and neither is left
            # showing a debt that no longer exists.
            pending = (downpayment.line_ids | credit_note.line_ids).filtered(
                lambda line: (
                    line.account_id.account_type == "asset_receivable"
                    and not line.reconciled
                )
            )
            for account in pending.account_id:
                pending.filtered(
                    lambda line, acc=account: line.account_id == acc
                ).reconcile()
        self.downpayment_reissue_partner_id = downpayments[:1].partner_id

    def action_post(self):
        res = super().action_post()
        for payment in self.filtered("downpayment_reissue_partner_id"):
            payment._reissue_downpayment()
        self._mark_downpayment_refund()
        return res

    def _reissue_downpayment(self):
        """Issue the down payment again, now for the amount the payment ended up
        having.

        Deliberately synchronous, unlike the transfer of a down payment between
        customers: here re-issuing IS the operation. If it cannot be done -- the
        new amount goes over the simplified limit and the customer has no fiscal
        data -- the hotel has to decide, and the right answer is to refuse the
        change rather than to leave the folio quietly short of an invoice. The
        rectification is already committed by then, so refusing leaves the
        payment in draft with nothing to collect against, which is consistent.
        """
        self.ensure_one()
        partner = self.downpayment_reissue_partner_id
        self.downpayment_reissue_partner_id = False
        if not self.folio_ids or self.payment_type != "inbound":
            return self.env["account.move"]
        return self.env["account.payment"]._create_downpayment_invoice(self, partner.id)

    # ------------------------------------------------------------------
    # C1 -- a refund rectifies the down payment it gives back
    # ------------------------------------------------------------------
    def _mark_downpayment_refund(self):
        """Enqueue, never do it inline: giving money back to a guest is an
        operational act and it cannot fail because of what its invoice looks
        like. If the job fails the refund still stands, and pms_autoinvoice's
        own sweep is still there as a floor."""
        for payment in self:
            if payment.payment_type != "outbound" or payment.partner_type != "customer":
                continue
            if not payment.folio_ids:
                continue
            if not payment._get_downpayments_to_rectify():
                continue
            payment.downpayment_refund_state = "pending"
            payment.with_delay()._rectify_downpayments_for_refund()

    def _get_downpayments_to_rectify(self):
        """Live down payment invoices of this refund's folios, oldest first.

        Without this, pms_autoinvoice bills the refund as a brand new credit
        note off the folio wizard: a document with no ``reversed_entry_id``,
        which rectifies nothing and leaves the original down payment standing
        for its full amount."""
        self.ensure_one()
        downpayments = self.folio_ids.sale_line_ids.filtered(
            "is_downpayment"
        ).invoice_lines.move_id.filtered(
            lambda m: (
                m.state == "posted"
                and m.move_type == "out_invoice"
                and m.company_id == self.company_id
                and m._is_downpayment()
            )
        )
        rounding = self.company_id.currency_id.rounding
        downpayments = downpayments.filtered(
            lambda m: (
                float_compare(
                    m._amount_open_to_rectify(), 0.0, precision_rounding=rounding
                )
                > 0
            )
        )
        return downpayments.sorted(lambda m: (m.invoice_date or m.date, m.id))

    def _rectify_downpayments_for_refund(self):
        """Rectify the down payments this refund gives back, in order, for
        exactly what is being returned."""
        self.ensure_one()
        rounding = self.company_id.currency_id.rounding
        date = fields.Date.context_today(self)
        remaining = self.amount
        notes = []
        for downpayment in self._get_downpayments_to_rectify():
            if float_is_zero(remaining, precision_rounding=rounding):
                break
            amount = min(remaining, downpayment._amount_open_to_rectify())
            partial = (
                float_compare(
                    amount, downpayment.amount_total, precision_rounding=rounding
                )
                < 0
            )
            credit_note = downpayment._rectify_downpayment(
                date,
                amount=amount if partial else None,
                ref=_("Refunded by payment %s", self.name),
            )
            self._reconcile_with_refund(credit_note)
            remaining -= amount
            notes.append(
                _(
                    "%(amount)s of %(name)s rectified as %(refund)s.",
                    amount=amount,
                    name=downpayment.name,
                    refund=credit_note.name,
                )
            )
        if not float_is_zero(remaining, precision_rounding=rounding):
            # More was given back than was ever invoiced as a down payment. The
            # rest is a refund of something else and is not this module's to
            # guess at.
            notes.append(
                _(
                    "%s of this refund does not correspond to any down payment "
                    "and was left unapplied.",
                    remaining,
                )
            )
            self.downpayment_refund_state = "manual"
        else:
            self.downpayment_refund_state = "done"
        self.downpayment_refund_message = "\n".join(notes)
        self.move_id.message_post(
            body=Markup("<p>%s</p><ul>%s</ul>")
            % (
                _("Down payment refund"),
                Markup("").join(Markup("<li>%s</li>") % note for note in notes),
            )
        )
        return True

    def _reconcile_with_refund(self, credit_note):
        """Settle the credit note against the money actually paid back."""
        self.ensure_one()
        pending = (credit_note.line_ids | self.move_id.line_ids).filtered(
            lambda line: (
                line.account_id.account_type == "asset_receivable"
                and not line.reconciled
            )
        )
        for account in pending.account_id:
            pending.filtered(
                lambda line, acc=account: line.account_id == acc
            ).reconcile()

    def action_cancel(self):
        # A cancelled payment collects nothing, so there is nothing to re-issue.
        # Its down payment was already rectified on the way to draft.
        self.downpayment_reissue_partner_id = False
        return super().action_cancel()
