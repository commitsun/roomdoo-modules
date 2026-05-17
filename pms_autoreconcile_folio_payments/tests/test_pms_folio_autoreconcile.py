from datetime import datetime, timedelta

from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.pms.tests.common import TestPms


@tagged("post_install", "-at_install")
class TestPmsFolioAutoreconcile(TestPms, AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        user = cls.env["res.users"].browse(1)
        cls.env = cls.env(user=user)
        cls.payment_method_manual_in = cls.env.ref(
            "account.account_payment_method_manual_in"
        )
        cls.company_reconcile = cls.company_data_2["company"]
        cls.payment_journal = cls.env["account.journal"].create(
            {
                "name": "Test Payment Journal",
                "type": "bank",
                "company_id": cls.company_reconcile.id,
                "inbound_payment_method_line_ids": [
                    (6, 0, [cls.payment_method_manual_in.id])
                ],
            }
        )
        cls.invoice_journal = cls.env["account.journal"].create(
            {
                "name": "Test Invoice Journal",
                "code": "TEST_INV",
                "type": "sale",
                "company_id": cls.company_reconcile.id,
            }
        )

        cls.property = cls.env["pms.property"].create(
            {
                "name": "MY PMS TEST",
                "company_id": cls.company_reconcile.id,
                "journal_simplified_invoice_id": cls.invoice_journal.id,
                "default_pricelist_id": cls.pricelist1.id,
            }
        )
        cls.partner_id = cls.env["res.partner"].create(
            {
                "name": "Miguel",
                "vat": "45224522J",
                "country_id": cls.env.ref("base.es").id,
                "city": "Madrid",
                "zip": "28013",
                "street": "Calle de la calle",
            }
        )
        cls.sale_channel_direct1 = cls.env["pms.sale.channel"].create(
            {
                "name": "Door",
                "channel_type": "direct",
            }
        )
        cls.room_type_double = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.property.id],
                "name": "Double Test",
                "default_code": "DBL_Test",
                "class_id": cls.room_type_class1.id,
                "list_price": 25,
            }
        )

        cls.room1 = cls.env["pms.room"].create(
            {
                "pms_property_id": cls.property.id,
                "name": "Double 101",
                "room_type_id": cls.room_type_double.id,
                "capacity": 2,
            }
        )

    def test_autoreconcile_folio_payments_on_post(self):
        reservation = self.env["pms.reservation"].create(
            {
                "checkin": datetime.now(),
                "checkout": datetime.now() + timedelta(days=1),
                "adults": 2,
                "pms_property_id": self.property.id,
                "room_type_id": self.room_type_double.id,
                "partner_id": self.partner_id.id,
                "sale_channel_origin_id": self.sale_channel_direct1.id,
            }
        )

        invoice = reservation.folio_id._create_invoices()
        payment = self.env["account.payment"].create(
            {
                "payment_type": "inbound",
                "payment_method_id": self.payment_method_manual_in.id,
                "journal_id": self.payment_journal.id,
                "amount": reservation.folio_id.amount_total,
                "currency_id": reservation.folio_id.currency_id.id,
                "partner_id": reservation.folio_id.partner_id.id,
                "folio_ids": [(4, reservation.folio_id.id)],
            }
        )
        payment.action_post()
        invoice.action_post()
        self.assertEqual(
            invoice.payment_state,
            "paid",
            "The invoice should be marked as paid after posting the payment.",
        )

    def test_autoreconcile_folio_payments_do_payment(self):
        reservation = self.env["pms.reservation"].create(
            {
                "checkin": datetime.now(),
                "checkout": datetime.now() + timedelta(days=1),
                "adults": 2,
                "pms_property_id": self.property.id,
                "room_type_id": self.room_type_double.id,
                "partner_id": self.partner_id.id,
                "sale_channel_origin_id": self.sale_channel_direct1.id,
            }
        )

        invoice = reservation.folio_id._create_invoices()
        invoice.action_post()
        invoice.invalidate_recordset()
        reservation.folio_id.do_payment(
            payment_method_line=self.payment_journal.inbound_payment_method_line_ids[0],
            user=self.env.user,
            amount=reservation.folio_id.amount_total,
            folio=reservation.folio_id,
            partner=reservation.folio_id.partner_id,
        )
        self.assertEqual(
            invoice.payment_state,
            "paid",
            "The invoice should be marked as paid after do_payment is called.",
        )

    def test_autoreconcile_sole_invoice_partial_payment(self):
        """Tier 1: sole invoice in folio receives partial payment →
        auto-reconciled partially (payment_state='partial')."""
        reservation = self.env["pms.reservation"].create(
            {
                "checkin": datetime.now(),
                "checkout": datetime.now() + timedelta(days=1),
                "adults": 2,
                "pms_property_id": self.property.id,
                "room_type_id": self.room_type_double.id,
                "partner_id": self.partner_id.id,
                "sale_channel_origin_id": self.sale_channel_direct1.id,
            }
        )

        invoice = reservation.folio_id._create_invoices()
        invoice.action_post()

        partial_amount = reservation.folio_id.amount_total / 2
        pml = self.payment_journal.inbound_payment_method_line_ids[0]
        reservation.folio_id.do_payment(
            payment_method_line=pml,
            user=self.env.user,
            amount=partial_amount,
            folio=reservation.folio_id,
            partner=reservation.folio_id.partner_id,
        )

        self.assertEqual(
            invoice.payment_state,
            "partial",
            "Sole invoice should be partially reconciled" " with a partial payment.",
        )

    def test_sole_invoice_multiple_payments(self):
        """Tier 1: sole invoice with two payments that sum to the total →
        fully paid."""
        reservation = self.env["pms.reservation"].create(
            {
                "checkin": datetime.now(),
                "checkout": datetime.now() + timedelta(days=1),
                "adults": 2,
                "pms_property_id": self.property.id,
                "room_type_id": self.room_type_double.id,
                "partner_id": self.partner_id.id,
                "sale_channel_origin_id": self.sale_channel_direct1.id,
            }
        )
        folio = reservation.folio_id
        invoice = folio._create_invoices()

        total = folio.amount_total
        for amount in [total * 0.6, total * 0.4]:
            payment = self.env["account.payment"].create(
                {
                    "payment_type": "inbound",
                    "payment_method_id": self.payment_method_manual_in.id,
                    "journal_id": self.payment_journal.id,
                    "amount": amount,
                    "currency_id": folio.currency_id.id,
                    "partner_id": folio.partner_id.id,
                    "folio_ids": [(4, folio.id)],
                }
            )
            payment.action_post()

        invoice.action_post()
        self.assertEqual(
            invoice.payment_state,
            "paid",
            "Sole invoice should be fully paid when"
            " multiple payments sum to its amount.",
        )

    def test_ambiguous_same_amount(self):
        """Tier 2 blocked: two invoices with same residual + one matching
        payment → ambiguous, no auto-reconcile for either invoice."""
        res1 = self.env["pms.reservation"].create(
            {
                "checkin": datetime.now(),
                "checkout": datetime.now() + timedelta(days=1),
                "adults": 2,
                "pms_property_id": self.property.id,
                "room_type_id": self.room_type_double.id,
                "partner_id": self.partner_id.id,
                "sale_channel_origin_id": self.sale_channel_direct1.id,
            }
        )
        folio = res1.folio_id
        res2 = self.env["pms.reservation"].create(
            {
                "checkin": datetime.now() + timedelta(days=1),
                "checkout": datetime.now() + timedelta(days=2),
                "adults": 2,
                "folio_id": folio.id,
                "pms_property_id": self.property.id,
                "room_type_id": self.room_type_double.id,
                "partner_id": self.partner_id.id,
                "sale_channel_origin_id": self.sale_channel_direct1.id,
            }
        )
        # Invoice each reservation separately (same amount each)
        res1_lines = folio.sale_line_ids.filtered(
            lambda ln: ln.reservation_id == res1 and not ln.display_type
        )
        invoice1 = folio._create_invoices(
            lines_to_invoice={ln.id: ln.qty_to_invoice for ln in res1_lines}
        )
        res2_lines = folio.sale_line_ids.filtered(
            lambda ln: ln.reservation_id == res2 and not ln.display_type
        )
        invoice2 = folio._create_invoices(
            lines_to_invoice={ln.id: ln.qty_to_invoice for ln in res2_lines}
        )
        invoice1.action_post()
        invoice2.action_post()

        # One payment matching one invoice's amount (ambiguous)
        pml = self.payment_journal.inbound_payment_method_line_ids[0]
        folio.do_payment(
            payment_method_line=pml,
            user=self.env.user,
            amount=invoice1.amount_total,
            folio=folio,
            partner=folio.partner_id,
        )

        self.assertEqual(
            invoice1.payment_state,
            "not_paid",
            "Ambiguous: payment could match either"
            " invoice, should not auto-reconcile.",
        )
        self.assertEqual(
            invoice2.payment_state,
            "not_paid",
            "Ambiguous: payment could match either"
            " invoice, should not auto-reconcile.",
        )

    def test_unambiguous_different_amounts(self):
        """Tier 2: two invoices with different amounts + matching payments →
        both reconciled unambiguously."""
        res1 = self.env["pms.reservation"].create(
            {
                "checkin": datetime.now(),
                "checkout": datetime.now() + timedelta(days=1),
                "adults": 2,
                "pms_property_id": self.property.id,
                "room_type_id": self.room_type_double.id,
                "partner_id": self.partner_id.id,
                "sale_channel_origin_id": self.sale_channel_direct1.id,
            }
        )
        folio = res1.folio_id
        res2 = self.env["pms.reservation"].create(
            {
                "checkin": datetime.now() + timedelta(days=1),
                "checkout": datetime.now() + timedelta(days=3),
                "adults": 2,
                "folio_id": folio.id,
                "pms_property_id": self.property.id,
                "room_type_id": self.room_type_double.id,
                "partner_id": self.partner_id.id,
                "sale_channel_origin_id": self.sale_channel_direct1.id,
            }
        )
        # Invoice each reservation separately (25€ and 50€)
        res1_lines = folio.sale_line_ids.filtered(
            lambda ln: ln.reservation_id == res1 and not ln.display_type
        )
        invoice1 = folio._create_invoices(
            lines_to_invoice={ln.id: ln.qty_to_invoice for ln in res1_lines}
        )
        res2_lines = folio.sale_line_ids.filtered(
            lambda ln: ln.reservation_id == res2 and not ln.display_type
        )
        invoice2 = folio._create_invoices(
            lines_to_invoice={ln.id: ln.qty_to_invoice for ln in res2_lines}
        )
        invoice1.action_post()
        invoice2.action_post()

        # Payment matching invoice1 (25€)
        pml = self.payment_journal.inbound_payment_method_line_ids[0]
        folio.do_payment(
            payment_method_line=pml,
            user=self.env.user,
            amount=invoice1.amount_total,
            folio=folio,
            partner=folio.partner_id,
        )
        self.assertEqual(
            invoice1.payment_state,
            "paid",
            "Invoice should be paid" " (unambiguous exact match).",
        )

        # Payment matching invoice2 (50€)
        folio.do_payment(
            payment_method_line=pml,
            user=self.env.user,
            amount=invoice2.amount_total,
            folio=folio,
            partner=folio.partner_id,
        )
        self.assertEqual(
            invoice2.payment_state,
            "paid",
            "Invoice 50€ should be paid (sole remaining invoice).",
        )

    def test_partial_invoice_does_not_steal_pending_payment(self):
        """Bug fix: when the folio still has lines pending invoicing,
        Tier 1 must NOT kick in. A payment collected for a future invoice
        (e.g. tourist tax invoiced at night) must not be reconciled with
        the current invoice."""
        res1 = self.env["pms.reservation"].create(
            {
                "checkin": datetime.now(),
                "checkout": datetime.now() + timedelta(days=1),
                "adults": 2,
                "pms_property_id": self.property.id,
                "room_type_id": self.room_type_double.id,
                "partner_id": self.partner_id.id,
                "sale_channel_origin_id": self.sale_channel_direct1.id,
            }
        )
        folio = res1.folio_id
        self.env["pms.reservation"].create(
            {
                "checkin": datetime.now() + timedelta(days=1),
                "checkout": datetime.now() + timedelta(days=3),
                "adults": 2,
                "folio_id": folio.id,
                "pms_property_id": self.property.id,
                "room_type_id": self.room_type_double.id,
                "partner_id": self.partner_id.id,
                "sale_channel_origin_id": self.sale_channel_direct1.id,
            }
        )
        # Invoice only res1 (25€). res2 (50€) stays pending → folio is not
        # fully invoiced.
        res1_lines = folio.sale_line_ids.filtered(
            lambda ln: ln.reservation_id == res1 and not ln.display_type
        )
        invoice1 = folio._create_invoices(
            lines_to_invoice={ln.id: ln.qty_to_invoice for ln in res1_lines}
        )

        # Payment of 50€ collected up-front for the future invoice (res2).
        payment = self.env["account.payment"].create(
            {
                "payment_type": "inbound",
                "payment_method_id": self.payment_method_manual_in.id,
                "journal_id": self.payment_journal.id,
                "amount": 50,
                "currency_id": folio.currency_id.id,
                "partner_id": folio.partner_id.id,
                "folio_ids": [(4, folio.id)],
            }
        )
        payment.action_post()
        invoice1.action_post()

        self.assertIn(
            folio.invoice_status,
            ("to_invoice", "to_confirm"),
            "Folio must still have lines pending invoicing for this scenario.",
        )
        self.assertEqual(
            invoice1.payment_state,
            "not_paid",
            "Tier 1 must not steal payments meant for pending invoices.",
        )

    def test_partial_invoice_exact_match_reconciles(self):
        """Tier 2 with pending folio lines: when one of the available
        payments matches the invoice residual exactly, only that payment
        is reconciled. The remaining payment stays available for the
        future invoice."""
        res1 = self.env["pms.reservation"].create(
            {
                "checkin": datetime.now(),
                "checkout": datetime.now() + timedelta(days=1),
                "adults": 2,
                "pms_property_id": self.property.id,
                "room_type_id": self.room_type_double.id,
                "partner_id": self.partner_id.id,
                "sale_channel_origin_id": self.sale_channel_direct1.id,
            }
        )
        folio = res1.folio_id
        self.env["pms.reservation"].create(
            {
                "checkin": datetime.now() + timedelta(days=1),
                "checkout": datetime.now() + timedelta(days=3),
                "adults": 2,
                "folio_id": folio.id,
                "pms_property_id": self.property.id,
                "room_type_id": self.room_type_double.id,
                "partner_id": self.partner_id.id,
                "sale_channel_origin_id": self.sale_channel_direct1.id,
            }
        )
        # Invoice only res1 (25€); res2 (50€) stays pending.
        res1_lines = folio.sale_line_ids.filtered(
            lambda ln: ln.reservation_id == res1 and not ln.display_type
        )
        invoice1 = folio._create_invoices(
            lines_to_invoice={ln.id: ln.qty_to_invoice for ln in res1_lines}
        )

        # Two payments: 25€ (for invoice1) and 50€ (for the future invoice).
        for amount in [25, 50]:
            payment = self.env["account.payment"].create(
                {
                    "payment_type": "inbound",
                    "payment_method_id": self.payment_method_manual_in.id,
                    "journal_id": self.payment_journal.id,
                    "amount": amount,
                    "currency_id": folio.currency_id.id,
                    "partner_id": folio.partner_id.id,
                    "folio_ids": [(4, folio.id)],
                }
            )
            payment.action_post()
        invoice1.action_post()

        self.assertEqual(
            invoice1.payment_state,
            "paid",
            "Tier 2 must reconcile the exact-match payment even when the"
            " folio still has pending lines.",
        )

    def test_subset_sum_match(self):
        """Tier 3: unique subset of payment lines sums to invoice amount."""
        res1 = self.env["pms.reservation"].create(
            {
                "checkin": datetime.now(),
                "checkout": datetime.now() + timedelta(days=1),
                "adults": 2,
                "pms_property_id": self.property.id,
                "room_type_id": self.room_type_double.id,
                "partner_id": self.partner_id.id,
                "sale_channel_origin_id": self.sale_channel_direct1.id,
            }
        )
        folio = res1.folio_id
        res2 = self.env["pms.reservation"].create(
            {
                "checkin": datetime.now() + timedelta(days=1),
                "checkout": datetime.now() + timedelta(days=3),
                "adults": 2,
                "folio_id": folio.id,
                "pms_property_id": self.property.id,
                "room_type_id": self.room_type_double.id,
                "partner_id": self.partner_id.id,
                "sale_channel_origin_id": self.sale_channel_direct1.id,
            }
        )
        # Invoice each reservation separately: 25€ and 50€
        res1_lines = folio.sale_line_ids.filtered(
            lambda ln: ln.reservation_id == res1 and not ln.display_type
        )
        invoice1 = folio._create_invoices(
            lines_to_invoice={ln.id: ln.qty_to_invoice for ln in res1_lines}
        )
        res2_lines = folio.sale_line_ids.filtered(
            lambda ln: ln.reservation_id == res2 and not ln.display_type
        )
        invoice2 = folio._create_invoices(
            lines_to_invoice={ln.id: ln.qty_to_invoice for ln in res2_lines}
        )
        invoice1.action_post()
        invoice2.action_post()

        # 3 payments: 10 + 15 + 50 = 75 (= 25 + 50)
        # Invoice1 (25€) → subset {10+15} via Tier 3
        # Invoice2 (50€) → {50} via Tier 1/2
        for amount in [10, 15, 50]:
            payment = self.env["account.payment"].create(
                {
                    "payment_type": "inbound",
                    "payment_method_id": self.payment_method_manual_in.id,
                    "journal_id": self.payment_journal.id,
                    "amount": amount,
                    "currency_id": folio.currency_id.id,
                    "partner_id": folio.partner_id.id,
                    "folio_ids": [(4, folio.id)],
                }
            )
            payment.action_post()

        # Trigger autoreconcile explicitly (payments were created without
        # do_payment, so the folio hook did not fire)
        invoice1._autoreconcile_folio_payments()
        self.assertEqual(
            invoice1.payment_state,
            "paid",
            "Invoice 25€ should match subset {10+15} via Tier 3.",
        )

        invoice2._autoreconcile_folio_payments()
        self.assertEqual(
            invoice2.payment_state,
            "paid",
            "Invoice 50€ should match remaining payment via Tier 1.",
        )
