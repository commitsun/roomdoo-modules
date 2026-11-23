from datetime import date

from freezegun import freeze_time

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.pms_ai_tool.tools import PARAM_WRITE_ENABLED, get_guard

from .common import AdminToolsMixin

TOOL = "pms_folio_payment_move"


@tagged("post_install", "-at_install")
class TestPmsFolioPaymentMove(AdminToolsMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_admin()
        cls.customer = cls.env["res.partner"].create({"name": "AI Payment Guest"})
        with freeze_time("2026-09-01"):
            cls.wrong = cls._folio(cls.prop, 20, [100, 100])
            cls.right = cls._folio(cls.prop, 24, [150])
            cls.foreign = cls._folio(cls.other_prop, 20, [90])
        cls.journal = cls.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", cls.env.company.id)], limit=1
        )
        cls.payment = cls._pay(cls.wrong, 80)
        cls.env.invalidate_all()
        cls.tool = cls.env.ref(f"pms_ai_tool_admin.ai_tool_{TOOL}")
        cls.tool.write(
            {
                "allowed_user_ids": [(6, 0, (cls.reader | cls.reader_both).ids)],
                "write_enabled": True,
            }
        )
        cls.env["ir.config_parameter"].sudo().set_param(PARAM_WRITE_ENABLED, "True")

    @classmethod
    def _folio(cls, prop, first_day, prices):
        reservation = cls.env["pms.reservation"].create(
            {
                "pms_property_id": prop.id,
                "room_type_id": cls.room_type.id,
                "partner_id": cls.customer.id,
                "sale_channel_origin_id": cls.direct.id,
                "reservation_line_ids": [
                    (0, 0, {"date": date(2026, 9, first_day + n), "price": price})
                    for n, price in enumerate(prices)
                ],
            }
        )
        reservation.action_confirm()
        return reservation.folio_id

    @classmethod
    def _pay(cls, folio, amount):
        payment = cls.env["account.payment"].create(
            {
                "payment_type": "inbound",
                "partner_type": "customer",
                "partner_id": cls.customer.id,
                "amount": amount,
                "journal_id": cls.journal.id,
                "date": "2026-09-15",
                "folio_ids": [(6, 0, folio.ids)],
            }
        )
        payment.action_post()
        return payment

    def _preview(self, **kwargs):
        kwargs.setdefault("from_folio", str(self.wrong.id))
        kwargs.setdefault("to_folio", str(self.right.id))
        return self._run(TOOL, **kwargs)

    def _confirm(self, preview, **kwargs):
        kwargs.setdefault("from_folio", str(self.wrong.id))
        kwargs.setdefault("to_folio", str(self.right.id))
        token = preview["confirmation"]["confirmation_token"]
        return self._run(TOOL, confirm=True, confirmation_token=token, **kwargs)

    def test_preview_changes_nothing_and_apply_moves_the_payment(self):
        wrong_before = self.wrong.pending_amount
        right_before = self.right.pending_amount
        self.assertAlmostEqual(right_before, self.right.amount_total, 2)
        preview = self._preview()
        self.assertEqual(self.payment.folio_ids, self.wrong)
        self.assertEqual(self.payment.move_id.folio_ids, self.wrong)
        self.assertTrue(preview["confirmation"]["required"])
        result = preview["result"]
        self.assertEqual(result["payment"]["id"], self.payment.id)
        self.assertAlmostEqual(result["payment"]["amount"], 80, 2)
        self.assertAlmostEqual(result["from_folio"]["pending_before"], wrong_before, 2)
        self.assertAlmostEqual(
            result["from_folio"]["pending_after"], wrong_before + 80, 2
        )
        to_folio = result["to_folio"]
        self.assertAlmostEqual(to_folio["pending_before"], right_before, 2)
        self.assertAlmostEqual(to_folio["pending_after"], right_before - 80, 2)
        self.assertEqual(preview["warnings"], [])

        applied = self._confirm(preview)

        self.assertEqual(applied["result"]["payment_id"], self.payment.id)
        self.assertEqual(self.payment.folio_ids, self.right)
        self.assertEqual(self.payment.move_id.folio_ids, self.right)
        receivable = self.payment.move_id.line_ids.filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
        )
        self.assertEqual(receivable.folio_ids, self.right)
        self.assertNotIn(self.payment, self.wrong.payment_ids)
        self.assertIn(self.payment, self.right.payment_ids)
        self.assertAlmostEqual(
            self.wrong.pending_amount, result["from_folio"]["pending_after"], 2
        )
        self.assertAlmostEqual(
            self.right.pending_amount, result["to_folio"]["pending_after"], 2
        )
        # Moved once: a replay finds no payment on the first folio.
        with self.assertRaises(UserError):
            self._confirm(preview)
        self.assertEqual(self.payment.folio_ids, self.right)

    def test_refusals(self):
        with self.assertRaisesRegex(UserError, "already on the folio"):
            self._preview(to_folio=str(self.wrong.id))
        # Out of the user's properties, as source or as target.
        with self.assertRaisesRegex(UserError, "No folio found"):
            self._preview(to_folio=str(self.foreign.id))
        with self.assertRaisesRegex(UserError, "No folio found"):
            self._preview(from_folio=str(self.foreign.id))
        with self.assertRaisesRegex(UserError, "no posted payments"):
            self._preview(from_folio=str(self.right.id), to_folio=str(self.wrong.id))
        with self.assertRaisesRegex(UserError, "is not a posted payment"):
            self._preview(payment_id=self.payment.id + 10**6)
        second = self._pay(self.wrong, 20)
        with self.assertRaisesRegex(UserError, "several payments"):
            self._preview()
        preview = self._preview(payment_id=second.id)
        self.assertEqual(preview["result"]["payment"]["id"], second.id)
        for kwargs in ({"extra": 1}, {"to_folio": 12}, {"payment_id": "1"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(UserError):
                self._preview(**kwargs)
        self.assertEqual(self.payment.folio_ids, self.wrong)
        self.assertEqual(second.folio_ids, self.wrong)

    def test_reconciled_or_shared_payments_are_refused(self):
        self.payment.folio_ids = self.wrong | self.right
        with self.assertRaisesRegex(UserError, "several folios"):
            self._preview(payment_id=self.payment.id)
        self.payment.folio_ids = self.wrong
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.customer.id,
                "invoice_line_ids": [
                    (0, 0, {"name": "AI stay", "quantity": 1, "price_unit": 80})
                ],
            }
        )
        invoice.action_post()
        (invoice.line_ids + self.payment.move_id.line_ids).filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
        ).reconcile()
        with self.assertRaisesRegex(UserError, "already reconciled"):
            self._preview()

    def test_confirmation_step(self):
        preview = self._preview()
        token = preview["confirmation"]["confirmation_token"]
        with self.assertRaisesRegex(UserError, "Missing confirmation_token"):
            self._run(
                TOOL,
                from_folio=str(self.wrong.id),
                to_folio=str(self.right.id),
                confirm=True,
            )
        with self.assertRaisesRegex(UserError, "does not match"):
            self._run(
                TOOL,
                user=self.reader_both,
                from_folio=str(self.wrong.id),
                to_folio=str(self.right.id),
                confirm=True,
                confirmation_token=token,
            )
        # The data changed since the preview: a new payment on the folio.
        self.right.invalidate_recordset()
        self._pay(self.right, 10)
        with self.assertRaisesRegex(UserError, "does not match"):
            self._confirm(preview)
        self.assertEqual(self.payment.folio_ids, self.wrong)
        self._confirm(self._preview())
        self.assertEqual(self.payment.folio_ids, self.right)

    def test_it_is_a_write_tool(self):
        guard = get_guard(self.env["pms.ai.tools"]._ai_pms_folio_payment_move)
        self.assertTrue(guard["write"])
        self.assertFalse(guard["log_result"])
        self.assertEqual(guard["apply"], "_apply_pms_folio_payment_move")
        self.assertEqual(self.tool.guard_mode, "write")
        self.assertTrue(self.tool.description.startswith("SENSITIVE"))
        self.assertIn("confirm=true with the token", self.tool.description)
