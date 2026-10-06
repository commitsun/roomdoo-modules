from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged

from .common import DownpaymentCase


@tagged("post_install", "-at_install")
class TestRectificationSiiRefundType(DownpaymentCase):
    """Every credit note of this module goes through _rectify_downpayment, so
    that is where the SII refund type has to be given."""

    def test_rectification_passes_the_sii_refund_type(self):
        """The credit note copies the down payment, whose sii_refund_type is
        empty, so the SII module only sets 'I' when the context carries it.
        Without it the AEAT rejects the credit note (TipoRectificativa)."""
        folio = self._folio()
        downpayment, _line = self._downpayment(folio, self.anonymous, 50.0)
        move_class = type(self.env["account.move"])
        reverse_moves = move_class._reverse_moves
        seen = []

        def spy(moves, *args, **kwargs):
            seen.append(moves.env.context.get("sii_refund_type"))
            return reverse_moves(moves, *args, **kwargs)

        with patch.object(move_class, "_reverse_moves", spy):
            credit_note = downpayment._rectify_downpayment(fields.Date.today())

        self.assertEqual(seen, ["I"])
        self.assertEqual(credit_note.reversed_entry_id, downpayment)
        if "sii_refund_type" in credit_note._fields:
            self.assertEqual(credit_note.sii_refund_type, "I")
