from odoo import _, fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    def _post(self, soft=True):
        """A replacement issued through the API takes over, when it is posted,
        the money that the invoice it replaces collected: that one was
        rectified keeping its payment, and its credit note has held the money
        open since.

        It is posted without the folio autoreconciliation, so that this money
        reaches it first, through a transfer entry, before any other open
        payment of the folio is proposed to it.
        """
        replacements = self.filtered(lambda move: move._replaced_open_credit_notes())
        if not replacements:
            return super()._post(soft=soft)
        for move in replacements:
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
        for move in replacements.filtered(lambda m: m.state == "posted"):
            for credit_note in move._replaced_open_credit_notes():
                transfer = move._settle_rectification(credit_note, date)
                move.replaces_invoice_id._post_reissue_message(
                    credit_note, move, transfer
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
