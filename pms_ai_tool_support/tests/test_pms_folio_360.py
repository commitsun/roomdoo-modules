import json

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import tagged

from odoo.addons.pms_ai_tool.tools import get_guard

from .common import GUEST_EMAIL, SupportToolsCase


@tagged("post_install", "-at_install")
class TestPmsFolio360(SupportToolsCase):
    def _folio_360(self, **kwargs):
        return self._run("pms_folio_360", **kwargs)

    def test_envelope_and_folio(self):
        response = self._folio_360(folio_code=self.folio.name)
        self.assertEqual(response["tool"], "pms_folio_360")
        self.assertEqual(response["environment"], "test")
        for key in ("generated_at", "summary", "period", "definitions", "warnings"):
            self.assertIn(key, response)
        self.assertTrue(response["definitions"])
        self.assertIn(self.folio.name, response["summary"])
        info = response["result"]["folio"]
        self.assertEqual(info["id"], self.folio.id)
        self.assertEqual(info["code"], self.folio.name)
        self.assertEqual(info["state"], "confirm")
        self.assertEqual(info["state_label"], "Confirmed")
        self.assertEqual(
            info["property"], {"id": self.prop.id, "name": "AI support hotel"}
        )
        self.assertEqual(info["agency"], "AI agency")
        self.assertEqual(info["sale_channel"], "AI online")
        self.assertEqual(
            info["first_checkin"], fields.Date.to_string(self.folio.first_checkin)
        )
        self.assertEqual(
            info["last_checkout"], fields.Date.to_string(self.folio.last_checkout)
        )
        # Sold on 2026-09-01 00:00 UTC: Madrid is two hours ahead in September.
        self.assertEqual(info["created"], "2026-09-01T02:00+02:00")
        by_id = self._folio_360(folio_id=self.folio.id)
        self.assertEqual(by_id["result"]["folio"]["id"], self.folio.id)
        by_suffix = self._folio_360(folio_code=f"{self.folio.name} (3)")
        self.assertEqual(by_suffix["result"]["folio"]["id"], self.folio.id)

    def test_reservations(self):
        result = self._folio_360(folio_id=self.folio.id)["result"]
        rows = {row["id"]: row for row in result["reservations"]}
        self.assertEqual(set(rows), {self.res1.id, self.res2.id, self.res3.id})
        first = rows[self.res1.id]
        self.assertEqual(first["state"], "confirm")
        self.assertEqual(first["room_type"], "AI double")
        self.assertEqual(first["nights"], 2)
        self.assertEqual(first["adults"], self.res1.adults)
        self.assertEqual(first["rooms"], self.res1.rooms or None)
        self.assertEqual(first["room_with_vat"], round(self.res1.price_total, 2))
        self.assertEqual(first["room_without_vat"], round(self.res1.price_subtotal, 2))
        self.assertIsNone(first["board_service"])
        self.assertEqual(rows[self.res2.id]["board_service"], "AI breakfast")
        self.assertEqual(rows[self.res3.id]["state"], "cancel")
        counts = {
            row["state"]: row["count"]
            for row in result["reservation_counts"]["by_state"]
        }
        self.assertEqual(counts, {"confirm": 2, "cancel": 1})
        self.assertEqual(result["reservation_counts"]["total"], 3)

    def test_amounts(self):
        amounts = self._folio_360(folio_id=self.folio.id)["result"]["amounts"]
        self.assertGreater(amounts["with_vat"], 0)
        self.assertEqual(amounts["with_vat"], round(self.folio.amount_total, 2))
        self.assertEqual(amounts["without_vat"], round(self.folio.amount_untaxed, 2))
        self.assertEqual(amounts["vat"], round(self.folio.amount_tax, 2))
        self.assertAlmostEqual(
            amounts["with_vat"] - amounts["without_vat"], amounts["vat"], places=2
        )
        self.assertEqual(amounts["pending"], round(self.folio.pending_amount, 2))
        self.assertEqual(amounts["paid"], round(self.folio.invoices_paid, 2))
        self.assertEqual(amounts["currency"], self.folio.currency_id.name)

    def test_payments_and_invoices(self):
        result = self._folio_360(folio_id=self.folio.id)["result"]
        rows = result["payments"]["by_journal_and_method"]
        bank = [row for row in rows if row["journal"] == self.bank_journal.name]
        cash = [row for row in rows if row["journal"] == self.cash_journal.name]
        # 150 collected and 20 given back; the draft payment of 999 is ignored.
        self.assertEqual(len(bank), 1)
        self.assertEqual(
            (bank[0]["payments"], bank[0]["refunds"], bank[0]["amount"]), (1, 1, 130.0)
        )
        self.assertEqual(len(cash), 1)
        self.assertEqual(cash[0]["method"], "Statement line")
        self.assertEqual(cash[0]["amount"], 40.0)
        self.assertEqual(result["payments"]["net_total"], 170.0)
        invoices = result["invoices"]
        self.assertEqual(
            [(row["type"], row["with_vat"]) for row in invoices],
            [("out_invoice", 100.0), ("out_refund", -30.0)],
        )
        self.assertEqual(invoices[0]["number"], self.invoice.name)
        self.assertEqual(invoices[0]["state"], "posted")

    def test_notification_counts(self):
        notifications = self._folio_360(folio_id=self.folio.id)["result"][
            "notifications"
        ]
        by_state = {row["state"]: row["count"] for row in notifications["by_state"]}
        self.assertEqual(
            by_state,
            {"sent": 2, "error": 1, "skipped": 3, "pending": 2, "cancelled": 1},
        )
        self.assertEqual(notifications["total"], 9)
        self.assertEqual(
            {row["channel"]: row["count"] for row in notifications["by_channel"]},
            {"email": 9},
        )

    def test_personal_data_is_masked(self):
        response = self._folio_360(folio_id=self.folio.id)
        customer = response["result"]["customer"]
        self.assertEqual(customer["name"], "Jane Guest")
        self.assertEqual(customer["email"], "j***@example.com")
        self.assertEqual(customer["mobile"], "***222")
        dump = json.dumps(response)
        self.assertNotIn(GUEST_EMAIL, dump)
        self.assertNotIn("600 111 222", dump)
        self.assertNotIn("Jane", response["summary"])
        for name in ("_ai_pms_folio_360", "_ai_pms_folio_notifications"):
            guard = get_guard(getattr(self.env["pms.ai.tools"], name))
            self.assertFalse(guard["write"])
            self.assertFalse(guard["log_result"])

    def test_scope(self):
        for kwargs in ({"folio_id": self.folio.id}, {"folio_code": self.folio.name}):
            with self.assertRaisesRegex(UserError, "No folio found"):
                self._folio_360(user=self.stranger, **kwargs)
        own = self._folio_360(user=self.stranger, folio_id=self.other_folio.id)
        self.assertEqual(own["result"]["folio"]["id"], self.other_folio.id)
        with self.assertRaisesRegex(UserError, "No folio found"):
            self._folio_360(folio_code="999/99/999999")

    def test_same_code_in_two_properties(self):
        # Each property has its own sequence: codes can repeat across hotels.
        self.other_folio.name = self.folio.name
        with self.assertRaisesRegex(UserError, "Several folios have the code"):
            self._folio_360(user=self.both, folio_code=self.folio.name)
        own = self._folio_360(user=self.stranger, folio_code=self.folio.name)
        self.assertEqual(own["result"]["folio"]["id"], self.other_folio.id)
        own = self._folio_360(user=self.user, folio_code=self.folio.name)
        self.assertEqual(own["result"]["folio"]["id"], self.folio.id)

    def test_arguments(self):
        with self.assertRaisesRegex(UserError, "either folio_id or folio_code"):
            self._folio_360()
        with self.assertRaisesRegex(UserError, "either folio_id or folio_code"):
            self._folio_360(folio_id=self.folio.id, folio_code=self.folio.name)
        with self.assertRaisesRegex(UserError, "Unknown arguments"):
            self._folio_360(folio_id=self.folio.id, domain="[]")
        with self.assertRaisesRegex(UserError, "must be an integer"):
            self._folio_360(folio_id="1")
        with self.assertRaisesRegex(UserError, "longer than"):
            self._folio_360(folio_code="x" * 41)

    def test_user_without_accounting_rights(self):
        response = self._folio_360(user=self.limited, folio_id=self.folio.id)
        result = response["result"]
        self.assertEqual(result["folio"]["id"], self.folio.id)
        self.assertIsNone(result["payments"])
        self.assertIsNone(result["invoices"])
        self.assertEqual(result["notifications"]["total"], 9)
        for part in ("payments", "invoices"):
            self.assertTrue(
                any(f"no access to {part}" in text for text in response["warnings"])
            )

    def test_reservation_rows_are_limited(self):
        self.env["ir.config_parameter"].sudo().set_param("pms_ai_tool.max_rows", "2")
        response = self._folio_360(folio_id=self.folio.id)
        self.assertEqual(len(response["result"]["reservations"]), 2)
        self.assertEqual(response["result"]["reservation_counts"]["total"], 3)
        self.assertIn("Only the first 2 of 3 rows are shown.", response["warnings"])
