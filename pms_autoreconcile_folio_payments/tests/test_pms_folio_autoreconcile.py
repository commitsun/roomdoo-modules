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

    def test_no_autoreconcile_partial_payment(self):
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
        reservation.folio_id.do_payment(
            payment_method_line=self.payment_journal.inbound_payment_method_line_ids[0],
            user=self.env.user,
            amount=partial_amount,
            folio=reservation.folio_id,
            partner=reservation.folio_id.partner_id,
        )

        self.assertEqual(
            invoice.payment_state,
            "not_paid",
            "The invoice should not be marked as paid after a partial payment.",
        )
