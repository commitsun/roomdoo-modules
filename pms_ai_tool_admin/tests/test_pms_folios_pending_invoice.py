from datetime import date

from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged

from .common import AdminToolsMixin

PERIOD = {"date_from": "2026-09-20", "date_to": "2026-09-27"}


@tagged("post_install", "-at_install")
class TestPmsFoliosPendingInvoice(AdminToolsMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_admin()
        Partner = cls.env["res.partner"]
        cls.company = Partner.create({"name": "AI Company", "is_company": True})
        cls.person = Partner.create({"name": "AI Person"})
        # A tax id as found in real data, without the VAT check.
        cls.env.cr.execute(
            "UPDATE res_partner SET vat = 'ESB12345674' WHERE id = %s",
            [cls.company.id],
        )
        cls.anonymous = cls.env.ref("pms.various_pms_partner")
        with freeze_time("2026-09-01"):
            # Finished, paid, customer with a tax id: no hint at all.
            cls.f_ok = cls._folio(cls.prop, cls.company, 20, [100, 100])
            cls.f_anon = cls._folio(cls.prop, cls.anonymous, 21, [90])
            cls.f_nocust = cls._folio(cls.prop, cls.company, 21, [50])
            # No tax id and one of its two prices already invoiced.
            cls.f_novat = cls._folio(cls.prop, cls.person, 22, [100, 120])
            # The last reservation is cancelled: it must not set the checkout.
            cls.f_multi = cls._folio(cls.prop, cls.company, 24, [80])
            cancelled = cls._reservation(cls.prop, cls.company, 28, [80], cls.f_multi)
            cancelled.action_cancel()
            # Not checked out although it ended; ends after the period; fully
            # invoiced; in a property out of the reader's scope.
            cls.f_open = cls._folio(cls.prop, cls.company, 23, [70], done=False)
            cls.f_late = cls._folio(cls.prop, cls.company, 27, [60])
            cls.f_invoiced = cls._folio(cls.prop, cls.company, 22, [40])
            cls.f_other = cls._folio(cls.other_prop, cls.company, 22, [200])
        cls.env.flush_all()
        cls.env.cr.execute(
            "UPDATE pms_folio SET partner_id = NULL WHERE id = %s", [cls.f_nocust.id]
        )
        cls.env.invalidate_all()
        cls.folios = (
            cls.f_ok
            | cls.f_anon
            | cls.f_nocust
            | cls.f_novat
            | cls.f_multi
            | cls.f_open
            | cls.f_late
            | cls.f_invoiced
            | cls.f_other
        )
        cls._recompute(cls.folios)
        cls._invoice(cls.f_novat, cls._sale_lines(cls.f_novat)[:1])
        cls._invoice(cls.f_invoiced, cls._sale_lines(cls.f_invoiced))
        cls._pay(cls.f_ok)
        cls._recompute(cls.folios)
        cls.env.invalidate_all()
        cls.finished = cls.f_ok | cls.f_anon | cls.f_nocust | cls.f_novat | cls.f_multi

    @classmethod
    def _reservation(cls, prop, partner, first_day, prices, folio=None):
        """Reservation with a night per price from the given day of September."""
        vals = {
            "pms_property_id": prop.id,
            "room_type_id": cls.room_type.id,
            "partner_id": partner.id,
            "sale_channel_origin_id": cls.direct.id,
            "reservation_line_ids": [
                (0, 0, {"date": date(2026, 9, first_day + n), "price": price})
                for n, price in enumerate(prices)
            ],
        }
        if folio:
            vals["folio_id"] = folio.id
        return cls.env["pms.reservation"].create(vals)

    @classmethod
    def _folio(cls, prop, partner, first_day, prices, done=True):
        reservation = cls._reservation(prop, partner, first_day, prices)
        reservation.action_confirm()
        if done:
            reservation.write({"state": "done"})
        return reservation.folio_id

    @classmethod
    def _recompute(cls, folios):
        """Recompute, in order, what the PMS keeps stored on the folios.

        The PMS chains several stored computations that read each other; once
        the whole fixture is in place they are brought to what a request in
        the application would leave.
        """
        for name in (
            "amount_untaxed",
            "untaxed_amount_to_invoice",
            "payment_state",
            "force_nothing_to_invoice",
            "invoice_status",
        ):
            cls.env.add_to_compute(folios._fields[name], folios)
            folios.flush_recordset([name])

    @classmethod
    def _sale_lines(cls, folio):
        return folio.sale_line_ids.filtered(lambda line: not line.display_type)

    @classmethod
    def _invoice(cls, folio, sale_lines):
        move = cls.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": folio.partner_id.id,
                "invoice_date": "2026-09-23",
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": line.name,
                            "quantity": line.product_uom_qty,
                            "price_unit": line.price_unit,
                            "tax_ids": [(6, 0, line.tax_ids.ids)],
                            "folio_line_ids": [(6, 0, line.ids)],
                        },
                    )
                    for line in sale_lines
                ],
            }
        )
        move.action_post()
        return move

    @classmethod
    def _pay(cls, folio):
        journal = cls.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", cls.env.company.id)], limit=1
        )
        payment = cls.env["account.payment"].create(
            {
                "payment_type": "inbound",
                "partner_type": "customer",
                "partner_id": folio.partner_id.id,
                "amount": folio.amount_total,
                "journal_id": journal.id,
                "date": "2026-09-22",
                "folio_ids": [(6, 0, folio.ids)],
            }
        )
        payment.action_post()

    @staticmethod
    def _key(folio):
        """Folio names repeat from a property to another: key them by both."""
        return folio.pms_property_id.id, folio.name

    def _by_folio(self, response):
        return {
            (row["property_id"], row["folio"]): row
            for row in response["result"]["folios"]
        }

    def test_figures_and_reasons(self):
        response = self._run("pms_folios_pending_invoice", **PERIOD)
        self._assert_read_tool("pms_folios_pending_invoice", response)
        self.assertEqual(response["period"], PERIOD)
        rows = self._by_folio(response)
        self.assertEqual(
            set(rows), {self._key(f) for f in self.finished}, "Only finished folios"
        )
        expected = {
            self.f_ok: [],
            self.f_anon: ["no_customer", "payment_pending"],
            self.f_nocust: ["no_customer", "payment_pending"],
            self.f_novat: ["no_vat", "payment_pending", "partial_invoice"],
            self.f_multi: ["payment_pending"],
        }
        for folio, reasons in expected.items():
            row = rows[self._key(folio)]
            self.assertEqual(row["reasons"], reasons, folio.name)
            self.assertEqual(row["property_id"], self.prop.id)
            self.assertGreater(row["amount_to_invoice_untaxed"], 0)
            self.assertAlmostEqual(
                row["amount_to_invoice_untaxed"], folio.untaxed_amount_to_invoice, 2
            )
        self.assertEqual(rows[self._key(self.f_ok)]["checkout"], "2026-09-22")
        # The cancelled reservation of the last one does not move its checkout.
        self.assertEqual(rows[self._key(self.f_multi)]["checkout"], "2026-09-25")
        amounts = [r["amount_to_invoice_untaxed"] for r in response["result"]["folios"]]
        self.assertEqual(amounts, sorted(amounts, reverse=True))
        totals = response["result"]["totals"]
        self.assertEqual(totals["folios"], 5)
        self.assertAlmostEqual(
            totals["amount_to_invoice_untaxed"],
            sum(self.finished.mapped("untaxed_amount_to_invoice")),
            2,
        )
        self.assertEqual(
            totals["reasons"],
            {
                "no_customer": 2,
                "no_vat": 1,
                "payment_pending": 4,
                "partial_invoice": 1,
            },
        )
        (by_property,) = response["result"]["by_property"]
        self.assertEqual(by_property["property_id"], self.prop.id)
        self.assertEqual(by_property["property"], self.prop.name)
        self.assertEqual(by_property["folios"], 5)

    def test_folios_left_out(self):
        response = self._run("pms_folios_pending_invoice", **PERIOD)
        keys = set(self._by_folio(response))
        for folio in (self.f_open, self.f_late, self.f_invoiced, self.f_other):
            self.assertNotIn(self._key(folio), keys)
        # The folio that ended but was never checked out is reported apart.
        (warning,) = response["warnings"]
        self.assertIn("1 folios with an amount to invoice", warning)
        self.assertIn("not checked out", warning)

    def test_scope_by_property(self):
        both = self._run("pms_folios_pending_invoice", user=self.reader_both, **PERIOD)
        self.assertEqual(both["result"]["totals"]["folios"], 6)
        self.assertEqual(len(both["result"]["by_property"]), 2)
        self.assertIn(self._key(self.f_other), self._by_folio(both))
        only_other = self._run(
            "pms_folios_pending_invoice",
            user=self.reader_both,
            property_ids=[self.other_prop.id],
            **PERIOD,
        )
        self.assertEqual(list(self._by_folio(only_other)), [self._key(self.f_other)])
        with self.assertRaises(AccessError):
            self._run(
                "pms_folios_pending_invoice",
                property_ids=[self.other_prop.id],
                **PERIOD,
            )

    def test_period_filters_by_checkout(self):
        response = self._run(
            "pms_folios_pending_invoice", date_from="2026-09-26", date_to="2026-09-30"
        )
        self.assertEqual(list(self._by_folio(response)), [self._key(self.f_late)])

    def test_listing_is_limited(self):
        self.env["ir.config_parameter"].sudo().set_param("pms_ai_tool.max_rows", "2")
        response = self._run("pms_folios_pending_invoice", **PERIOD)
        self.assertEqual(len(response["result"]["folios"]), 2)
        self.assertEqual(response["result"]["totals"]["folios"], 5)
        self.assertTrue(any("first 2 of 5" in text for text in response["warnings"]))

    def test_validation(self):
        bad_calls = [
            {"date_from": "2026-01-01", "date_to": "2026-06-30"},
            {"date_from": "2026-09-27", "date_to": "2026-09-20"},
            {"date_from": "27/09/2026", "date_to": "2026-09-28"},
            {"date_from": "2026-09-20"},
            dict(PERIOD, domain="[]"),
            dict(PERIOD, property_ids=["1"]),
        ]
        for kwargs in bad_calls:
            with self.subTest(kwargs=kwargs), self.assertRaises(UserError):
                self._run("pms_folios_pending_invoice", **kwargs)
