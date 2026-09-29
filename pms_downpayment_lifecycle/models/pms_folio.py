from markupsafe import Markup

from odoo import _, fields, models
from odoo.tools import float_compare


class PmsFolio(models.Model):
    _inherit = "pms.folio"

    downpayment_cancel_state = fields.Selection(
        selection=[
            ("pending", "Pending"),
            ("done", "Done"),
            ("failed", "Failed"),
        ],
        string="Down payment cancellation",
        copy=False,
        readonly=True,
        index="btree_not_null",
        help="Set on a cancelled folio that still had a down payment invoice standing.",
    )
    downpayment_cancel_message = fields.Text(
        string="Down payment cancellation detail",
        copy=False,
        readonly=True,
    )

    def action_cancel(self):
        res = super().action_cancel()
        self._mark_downpayment_cancel()
        return res

    def _mark_downpayment_cancel(self):
        """Enqueue, never inline: cancelling is an operational act and a stay
        that is not going to happen cannot stay confirmed because an old invoice
        will not rectify.

        pms re-enters ``action_cancel`` -- cancelling the last reservation calls
        it back on the folio -- so this runs twice. It is idempotent: the second
        pass finds nothing left open to rectify."""
        for folio in self:
            if not folio._get_downpayments_to_cancel():
                continue
            folio.downpayment_cancel_state = "pending"
            folio.with_delay()._rectify_downpayments_for_cancel()

    def _get_downpayments_to_cancel(self):
        """Down payment invoices of this folio with something still standing."""
        self.ensure_one()
        rounding = self.company_id.currency_id.rounding
        downpayments = self.sale_line_ids.filtered(
            "is_downpayment"
        ).invoice_lines.move_id.filtered(
            lambda m: (
                m.state == "posted"
                and m.move_type == "out_invoice"
                and m.company_id == self.company_id
                and m._is_downpayment()
                # Never a document this module itself produced: an
                # out_invoice that reverses a credit note is a counter-invoice,
                # not a down payment, however much it looks like one.
                and not m.reversed_entry_id
            )
        )
        return downpayments.filtered(
            lambda m: (
                float_compare(
                    m._amount_open_to_rectify(), 0.0, precision_rounding=rounding
                )
                > 0
            )
        )

    def _rectify_downpayments_for_cancel(self):
        """Rectify in full what is left of every down payment on the folio.

        The credit notes are deliberately left open, in favour of the customer:
        the stay is not going to be invoiced, so nothing of the folio will
        absorb them. The cancellation penalty -- which pms has already added to
        the folio as a service line -- is invoiced as its own document and takes
        its part; whatever is still in favour afterwards is given back, and that
        refund settles against these very credit notes instead of rectifying
        anything again.
        """
        self.ensure_one()
        rounding = self.company_id.currency_id.rounding
        date = fields.Date.context_today(self)
        notes = []
        for downpayment in self._get_downpayments_to_cancel():
            standing = downpayment._amount_open_to_rectify()
            partial = (
                float_compare(
                    standing, downpayment.amount_total, precision_rounding=rounding
                )
                < 0
            )
            credit_note = downpayment._rectify_downpayment(
                date,
                amount=standing if partial else None,
                ref=_("Folio %s cancelled", self.name),
            )
            notes.append(
                _(
                    "%(name)s rectified as %(refund)s for %(amount)s.",
                    name=downpayment.name,
                    refund=credit_note.name,
                    amount=standing,
                )
            )
        self.downpayment_cancel_state = "done"
        self.downpayment_cancel_message = "\n".join(notes)
        self.message_post(
            body=Markup("<p>%s</p><ul>%s</ul>")
            % (
                _("Down payment cancellation"),
                Markup("").join(Markup("<li>%s</li>") % note for note in notes),
            )
        )
        return True
