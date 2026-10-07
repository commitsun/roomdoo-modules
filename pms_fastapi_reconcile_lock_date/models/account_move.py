from odoo import _, fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    rectification_released_line_ids = fields.Many2many(
        comodel_name="account.move.line",
        relation="account_move_rectification_released_line_rel",
        column1="move_id",
        column2="line_id",
        string="Payments released by the rectification",
        copy=False,
        readonly=True,
        help="Payment lines that settled this invoice until it was rectified "
        "the core way. The invoice that replaces it takes them over when it is "
        "posted.",
    )

    def _post(self, soft=True):
        """A replacement issued through the API takes over, when it is posted,
        the money that the invoice it replaces collected.

        Rectified keeping its payment, the original's credit note has held that
        money open since, and it reaches the replacement through a transfer
        entry. Rectified the core way, the original released its payments, and
        the replacement takes those same payments over.

        Either way it is posted without the folio autoreconciliation, so that
        this money reaches it first, before any other open payment of the folio
        is proposed to it.
        """
        transfer_replacements = self.filtered(
            lambda move: move._replaced_open_credit_notes()
        )
        core_replacements = (self - transfer_replacements).filtered(
            lambda move: move._replaced_released_payment_lines()
        )
        replacements = transfer_replacements | core_replacements
        if not replacements:
            return super()._post(soft=soft)
        for move in transfer_replacements:
            if not move._get_single_receivable_line():
                raise UserError(
                    _(
                        "%(invoice)s replaces %(original)s, whose money is held "
                        "by its credit note, but it is split in several customer "
                        "lines, so where to put that money is ambiguous. Give it "
                        "a payment term without instalments.",
                        invoice=move.display_name,
                        original=move.replaces_invoice_id.display_name,
                    )
                )
        posted = self.browse()
        if self - replacements:
            posted |= super(AccountMove, self - replacements)._post(soft=soft)
        posted |= super(
            AccountMove, replacements.with_context(skip_folio_autoreconcile=True)
        )._post(soft=soft)
        date = fields.Date.context_today(self)
        for move in transfer_replacements.filtered(lambda m: m.state == "posted"):
            for credit_note in move._replaced_open_credit_notes():
                transfer = move._settle_rectification(credit_note, date)
                move.replaces_invoice_id._post_reissue_message(
                    credit_note, move, transfer
                )
        for move in core_replacements.filtered(lambda m: m.state == "posted"):
            move._reconcile_released_payment_lines(
                move._replaced_released_payment_lines()
            )
        replacements._autoreconcile_folio_payments()
        return posted

    def _replaced_open_credit_notes(self):
        """The credit notes of the invoice this one replaces that still hold
        open the money it collected. A core reversal settles its credit note
        against the original, so there is none in that case."""
        self.ensure_one()
        if self.move_type != "out_invoice" or not self.replaces_invoice_id:
            return self.browse()
        return self.replaces_invoice_id.reversal_move_id.filtered(
            lambda credit_note: credit_note.state == "posted"
            and credit_note._get_single_receivable_line()
            and not credit_note._get_single_receivable_line().reconciled
        )

    def _replaced_released_payment_lines(self):
        """The payments that the core rectification of the invoice this one
        replaces released, still open: they are this one's to take."""
        self.ensure_one()
        if self.move_type != "out_invoice" or not self.replaces_invoice_id:
            return self.env["account.move.line"]
        return self.replaces_invoice_id.rectification_released_line_ids.filtered(
            lambda line: not line.reconciled
        )

    def _check_replacement_can_go(self):
        """A draft that replaces a rectified invoice is the only document that
        can take over the money its credit note holds: deleted or cancelled,
        that money would stay on the credit note with nothing to settle it."""
        drafts = self.filtered(
            lambda move: move.state == "draft" and move._replaced_open_credit_notes()
        )
        if drafts:
            raise UserError(
                _(
                    "%(invoice)s replaces %(original)s, and the money of that "
                    "invoice is waiting for it on its credit note. Edit it "
                    "instead of deleting or cancelling it.",
                    invoice=drafts[0].display_name,
                    original=drafts[0].replaces_invoice_id.display_name,
                )
            )

    def unlink(self):
        self._check_replacement_can_go()
        return super().unlink()

    def button_cancel(self):
        self._check_replacement_can_go()
        return super().button_cancel()
