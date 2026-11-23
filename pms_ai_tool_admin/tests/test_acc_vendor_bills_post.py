from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

from .test_acc_vendor_bill_edit import VendorBillWriteMixin

TOOL = "acc_vendor_bills_post"
POST_MODULE = "odoo.addons.pms_ai_tool_admin.models.acc_vendor_bills_post"


@tagged("post_install", "-at_install")
class TestAccVendorBillsPost(VendorBillWriteMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_vendor_bills(TOOL)
        cls.ready = cls._draft(cls.supplier_a, [100], cls.prop, "AI-POST-OK")
        cls.ready2 = cls._draft(cls.supplier_b, [40, 60], cls.prop, "AI-POST-OK2")
        cls.no_tax = cls._draft(
            cls.supplier_a, [70], cls.prop, "AI-POST-NOTAX", taxes=cls.tax21.browse()
        )
        cls.checked = cls._draft(
            cls.supplier_a, [80], cls.prop, "AI-POST-CHECK", to_check=True
        )
        cls.zero = cls._draft(cls.supplier_a, [0], cls.prop, "AI-POST-ZERO")
        cls.old = cls._draft(cls.supplier_b, [30], cls.prop, "AI-POST-DUP")
        cls.old.action_post()
        cls.dup = cls._draft(cls.supplier_b, [55], cls.prop, "ai-post-dup")
        cls.far = cls._draft(cls.supplier_a, [90], cls.other_prop, "AI-POST-FAR")
        cls.selection = (
            cls.ready | cls.ready2 | cls.no_tax | cls.checked | cls.zero | cls.dup
        )

    def _states(self, moves):
        moves.invalidate_recordset()
        return {move.id: move.state for move in moves}

    def test_preview_lists_ready_and_not_ready_and_changes_nothing(self):
        preview = self._preview(TOOL, bill_ids=self.selection.ids + self.old.ids)
        self._assert_write_tool(TOOL, preview)
        result = preview["result"]
        self.assertEqual(
            {row["bill_id"] for row in result["ready"]},
            set((self.ready | self.ready2).ids),
        )
        reasons = {row["bill_id"]: row["reasons"] for row in result["not_ready"]}
        self.assertEqual(reasons[self.no_tax.id], ["lines without taxes"])
        self.assertEqual(reasons[self.checked.id], ["flagged to check"])
        self.assertIn("total is not positive", reasons[self.zero.id])
        self.assertEqual(len(reasons[self.dup.id]), 1)
        self.assertIn("probable duplicate", reasons[self.dup.id][0])
        self.assertIn("not in draft", reasons[self.old.id][0])
        self.assertEqual(set(self._states(self.selection).values()), {"draft"})

    def test_apply_posts_only_the_ready_bills(self):
        response = self._apply(TOOL, bill_ids=self.selection.ids)
        posted = {row["bill_id"] for row in response["result"]["posted"]}
        self.assertEqual(posted, set((self.ready | self.ready2).ids))
        self.assertFalse(response["result"]["failed"])
        states = self._states(self.selection)
        self.assertEqual(states[self.ready.id], "posted")
        self.assertEqual(states[self.ready2.id], "posted")
        for move in self.no_tax | self.checked | self.zero | self.dup:
            self.assertEqual(states[move.id], "draft")
        self.assertTrue(all(row["number"] for row in response["result"]["posted"]))

    def test_a_failing_bill_does_not_stop_the_others(self):
        Move = type(self.env["account.move"])
        original = Move._post
        bad = self.ready2

        def _post(moves, soft=True):
            if bad in moves:
                raise UserError("AI simulated failure")
            return original(moves, soft)

        with patch.object(Move, "_post", _post):
            response = self._apply(TOOL, bill_ids=(self.ready | self.ready2).ids)
        self.assertEqual(
            [row["bill_id"] for row in response["result"]["posted"]], self.ready.ids
        )
        failed = response["result"]["failed"]
        self.assertEqual(failed[0]["bill_id"], self.ready2.id)
        self.assertIn("AI simulated failure", failed[0]["error"])
        states = self._states(self.ready | self.ready2)
        self.assertEqual(states, {self.ready.id: "posted", self.ready2.id: "draft"})

    def test_duplicates_within_the_selection(self):
        twin = self._draft(self.supplier_a, [100], self.prop, "AI-POST-TWIN")
        twin.invoice_date = self.ready.invoice_date
        preview = self._preview(TOOL, bills=["AI-POST-OK", "AI-POST-TWIN"])
        self.assertEqual(len(preview["result"]["ready"]), 1)
        self.assertIn(
            "in this selection", preview["result"]["not_ready"][0]["reasons"][0]
        )

    def test_by_property_and_dates(self):
        preview = self._preview(
            TOOL,
            property_ids=self.prop.ids,
            date_from="2026-09-01",
            date_to="2026-11-30",
        )
        bill_ids = {
            row["bill_id"]
            for row in preview["result"]["ready"] + preview["result"]["not_ready"]
        }
        self.assertEqual(bill_ids, set(self.selection.ids))
        with patch(f"{POST_MODULE}.MAX_POST", 3), self.assertRaisesRegex(
            UserError, "at most 3"
        ):
            self._preview(
                TOOL,
                property_ids=self.prop.ids,
                date_from="2026-09-01",
                date_to="2026-11-30",
            )

    def test_refusals(self):
        cases = [
            ({"bill_ids": self.far.ids}, "not found"),
            ({"bill_ids": list(range(1, 52))}, "at most 50"),
            ({}, "Give the bills"),
            (
                {"bill_ids": self.ready.ids, "date_from": "2026-09-01"},
                "not both",
            ),
            (
                {"date_from": "2026-01-01", "date_to": "2026-12-31"},
                "longer than",
            ),
            (
                {
                    "property_ids": self.other_prop.ids,
                    "date_from": "2026-09-01",
                    "date_to": "2026-09-30",
                },
                "no access|have no access",
            ),
        ]
        for kwargs, message in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(Exception, message):
                    self._preview(TOOL, **kwargs)
        # The bill of the other hotel is in scope for a user of both hotels.
        preview = self._preview(TOOL, user=self.reader_both, bill_ids=self.far.ids)
        self.assertEqual(len(preview["result"]["ready"]), 1)

    def test_confirmation_is_bound_to_the_preview(self):
        kwargs = {"bill_ids": (self.ready | self.ready2).ids}
        token = self._preview(TOOL, **kwargs)["confirmation"]["confirmation_token"]
        with self.assertRaises(UserError):
            self._run(TOOL, confirm=True, confirmation_token="garbage", **kwargs)
        self.ready2.to_check = True
        self.env.flush_all()
        with self.assertRaisesRegex(UserError, "does not match"):
            self._run(TOOL, confirm=True, confirmation_token=token, **kwargs)
        self.assertEqual(
            set(self._states(self.ready | self.ready2).values()), {"draft"}
        )
