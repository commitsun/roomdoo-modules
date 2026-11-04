from datetime import date

from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged

from .common import AdminToolsMixin

AS_OF = "2026-10-31"
BUCKETS = ("1_30", "31_60", "61_90", "over_90")


@tagged("post_install", "-at_install")
class TestPmsCustomerOverdue(AdminToolsMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_admin()
        Partner = cls.env["res.partner"]
        cls.agency = Partner.create(
            {
                "name": "AI Agency",
                "is_company": True,
                "is_agency": True,
                "sale_channel_id": cls.indirect.id,
            }
        )
        cls.corp = Partner.create({"name": "AI Corp", "is_company": True})
        cls.guest = Partner.create({"name": "AI Guest"})
        cls.anonymous = cls.env.ref("pms.various_pms_partner")
        # The database may hold open invoices of its own: take them out of the way.
        cls.env.cr.execute(
            "UPDATE account_move SET state = 'cancel' "
            "WHERE move_type = 'out_invoice' AND state = 'posted'"
        )
        cls.env.invalidate_all()
        # (partner, residual, days overdue on AS_OF, property); None: no property
        cls.open_invoices = []
        cls._add(cls.agency, 1000, "2026-10-20", cls.prop)
        partial = cls._invoice(cls.agency, 2000, "2026-09-20", cls.prop)
        cls._pay(partial, 500)
        cls.open_invoices.append((cls.agency, 1500, 41, cls.prop))
        cls._add(cls.corp, 3000, "2026-08-20", cls.prop)
        cls._add(cls.corp, 4000, "2026-07-01", cls.prop)
        cls._add(cls.corp, 10, "2026-10-15", None)
        cls._add(cls.guest, 100, "2026-10-25", cls.prop)
        cls._add(cls.guest, 75, "2026-09-30", cls.prop)
        cls._add(cls.anonymous, 50, "2026-10-01", cls.prop)
        cls._add(cls.corp, 5000, "2026-10-10", cls.other_prop)
        # Not overdue on AS_OF, due that very day, fully paid, draft
        cls._invoice(cls.corp, 700, "2026-11-15", cls.prop)
        cls._invoice(cls.corp, 60, AS_OF, cls.prop)
        paid = cls._invoice(cls.corp, 900, "2026-09-01", cls.prop)
        cls._pay(paid, 900)
        cls._invoice(cls.corp, 800, "2026-09-01", cls.prop, post=False)
        cls.env.invalidate_all()

    @classmethod
    def _add(cls, partner, amount, due, prop):
        cls._invoice(partner, amount, due, prop)
        days = (date.fromisoformat(AS_OF) - date.fromisoformat(due)).days
        cls.open_invoices.append((partner, amount, days, prop))

    @classmethod
    def _invoice(cls, partner, amount, due, prop, post=True):
        move = cls.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": partner.id,
                "invoice_date": "2026-06-15",
                "invoice_date_due": due,
                "invoice_payment_term_id": False,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "AI line",
                            "quantity": 1,
                            "price_unit": amount,
                            "tax_ids": [(6, 0, [])],
                        },
                    )
                ],
            }
        )
        if post:
            move.action_post()
        if prop:
            cls.env.cr.execute(
                "UPDATE account_move SET pms_property_id = %s WHERE id = %s",
                [prop.id, move.id],
            )
        return move

    @classmethod
    def _pay(cls, invoice, amount):
        journal = cls.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", cls.env.company.id)], limit=1
        )
        payment = cls.env["account.payment"].create(
            {
                "payment_type": "inbound",
                "partner_type": "customer",
                "partner_id": invoice.partner_id.id,
                "amount": amount,
                "journal_id": journal.id,
                "date": "2026-09-22",
            }
        )
        payment.action_post()
        lines = (invoice.line_ids + payment.move_id.line_ids).filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
        )
        lines.reconcile()

    @staticmethod
    def _bucket(days):
        if days <= 30:
            return "1_30"
        if days <= 60:
            return "31_60"
        if days <= 90:
            return "61_90"
        return "over_90"

    def _expected(self, prop_ids, kind="all"):
        """Amount and invoices by bucket of the fixture, from the partner flags."""
        figures = {key: [0.0, 0] for key in BUCKETS}
        for partner, residual, days, prop in self.open_invoices:
            if (prop.id if prop else None) not in prop_ids:
                continue
            if kind == "agency" and not partner.is_agency:
                continue
            if kind == "company" and not (partner.is_company and not partner.is_agency):
                continue
            figures[self._bucket(days)][0] += residual
            figures[self._bucket(days)][1] += 1
        return figures

    def _assert_buckets(self, metrics, expected):
        for key in BUCKETS:
            self.assertEqual(
                metrics["buckets"][key],
                {"amount": expected[key][0], "invoices": expected[key][1]},
                key,
            )
        self.assertEqual(
            metrics["overdue_amount"], sum(value[0] for value in expected.values())
        )
        self.assertEqual(
            metrics["invoices"], sum(value[1] for value in expected.values())
        )

    def test_aging_buckets_by_property(self):
        response = self._run("pms_customer_overdue", as_of=AS_OF)
        self._assert_read_tool("pms_customer_overdue", response)
        self.assertEqual(response["period"], {"as_of": AS_OF})
        self._assert_buckets(
            response["result"]["totals"], self._expected({self.prop.id, None})
        )
        # The expectation above includes the invoice without property.
        self.assertEqual(response["result"]["totals"]["overdue_amount"], 9735.0)
        rows = {row["property_id"]: row for row in response["result"]["by_property"]}
        self.assertEqual(set(rows), {self.prop.id, None})
        self.assertEqual(rows[self.prop.id]["property"], self.prop.name)
        self.assertEqual(rows[self.prop.id]["overdue_amount"], 9725.0)
        self.assertEqual(rows[self.prop.id]["invoices"], 7)
        self.assertEqual(rows[None]["overdue_amount"], 10.0)
        self.assertEqual(rows[None]["property"], "No property")
        # Boundaries: 30 days overdue is still the first bucket, 31 the second.
        buckets = rows[self.prop.id]["buckets"]
        self.assertEqual(buckets["1_30"], {"amount": 1150.0, "invoices": 3})
        self.assertEqual(buckets["31_60"], {"amount": 1575.0, "invoices": 2})
        self.assertEqual(buckets["61_90"], {"amount": 3000.0, "invoices": 1})
        self.assertEqual(buckets["over_90"], {"amount": 4000.0, "invoices": 1})

    def test_top_debtors(self):
        response = self._run("pms_customer_overdue", as_of=AS_OF)
        debtors = response["result"]["top_debtors"]
        self.assertEqual(
            [(d["partner_id"], d["overdue_amount"], d["invoices"]) for d in debtors],
            [
                (self.corp.id, 7010.0, 3),
                (self.agency.id, 2500.0, 2),
                (self.guest.id, 175.0, 2),
                (self.anonymous.id, 50.0, 1),
            ],
        )
        corp, agency, guest, anonymous = debtors
        self.assertEqual(corp["kind"], "company")
        self.assertEqual(corp["oldest_due_date"], "2026-07-01")
        self.assertEqual(corp["max_days_overdue"], 122)
        self.assertEqual(agency["kind"], "agency")
        self.assertEqual(guest["kind"], "person")
        self.assertEqual(
            [d["anonymous_customer"] for d in debtors], [False, False, False, True]
        )
        self.assertEqual(anonymous["max_days_overdue"], 30)

    def test_partner_kind(self):
        for kind in ("agency", "company"):
            with self.subTest(kind=kind):
                response = self._run(
                    "pms_customer_overdue", as_of=AS_OF, partner_kind=kind
                )
                expected = self._expected({self.prop.id, None}, kind)
                self._assert_buckets(response["result"]["totals"], expected)
        agencies = self._run("pms_customer_overdue", as_of=AS_OF, partner_kind="agency")
        self.assertEqual(
            [d["partner_id"] for d in agencies["result"]["top_debtors"]],
            [self.agency.id],
        )
        self.assertEqual(agencies["result"]["totals"]["overdue_amount"], 2500.0)
        companies = self._run(
            "pms_customer_overdue", as_of=AS_OF, partner_kind="company"
        )
        self.assertNotIn(
            self.agency.id,
            [d["partner_id"] for d in companies["result"]["top_debtors"]],
        )

    def test_scope_by_property(self):
        both = self._run("pms_customer_overdue", user=self.reader_both, as_of=AS_OF)
        self.assertEqual(both["result"]["totals"]["overdue_amount"], 14735.0)
        self.assertEqual(len(both["result"]["by_property"]), 3)
        only_other = self._run(
            "pms_customer_overdue",
            user=self.reader_both,
            as_of=AS_OF,
            property_ids=[self.other_prop.id],
        )
        self.assertEqual(only_other["result"]["totals"]["overdue_amount"], 5000.0)
        self.assertEqual(
            [row["property_id"] for row in only_other["result"]["by_property"]],
            [self.other_prop.id],
        )
        with self.assertRaises(AccessError):
            self._run(
                "pms_customer_overdue",
                as_of=AS_OF,
                property_ids=[self.other_prop.id],
            )

    def test_needs_accounting_access(self):
        with self.assertRaises(AccessError):
            self._run("pms_customer_overdue", user=self.pms_only, as_of=AS_OF)

    def test_default_date_is_today(self):
        with freeze_time("2026-10-31 12:00:00"):
            response = self._run("pms_customer_overdue")
        self.assertEqual(response["period"], {"as_of": AS_OF})
        # An invoice due on that day is not overdue yet, one due before is.
        self.assertEqual(response["result"]["totals"]["overdue_amount"], 9735.0)
        with freeze_time("2026-11-01 12:00:00"):
            later = self._run("pms_customer_overdue")
        self.assertEqual(later["result"]["totals"]["overdue_amount"], 9795.0)

    def test_marked_sensitive(self):
        tool = self.env.ref("pms_ai_tool_admin.ai_tool_pms_customer_overdue")
        self.assertTrue(tool.description.startswith("SENSITIVE"))

    def test_validation(self):
        bad_calls = [
            {"as_of": "31/10/2026"},
            {"as_of": 20261031},
            {"partner_kind": "guest"},
            {"property_ids": "1"},
            {"as_of": AS_OF, "domain": "[]"},
        ]
        for kwargs in bad_calls:
            with self.subTest(kwargs=kwargs), self.assertRaises(UserError):
                self._run("pms_customer_overdue", **kwargs)
