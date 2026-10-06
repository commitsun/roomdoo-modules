# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Editing a validated invoice through the API (PUT /invoices/{id} with
confirmRefund=true) must not unreconcile it: the original keeps its payment,
and the money reaches the replacement when it is validated."""
import datetime
from unittest.mock import patch

from fastapi import status

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi


@tagged("post_install", "-at_install")
class TestInvoiceRectification(CommonTestPmsApi):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.test_company
        if not company.chart_template_id:
            coa = cls.env.ref("l10n_generic_coa.configurable_chart_template", False)
            if not coa:
                cls.skipTest(cls, "No chart of accounts available.")
            coa.try_loading(company=company, install_demo=False)
        cls.env.user.write(
            {"company_ids": [Command.link(company.id)], "company_id": company.id}
        )
        cls.env = cls.env(
            context=dict(cls.env.context, allowed_company_ids=company.ids)
        )
        room_type_class = cls.env["pms.room.type.class"].create(
            {"name": "Standard", "default_code": "STD"}
        )
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.test_property.id],
                "name": "Double Test",
                "default_code": "DBL_Test",
                "class_id": room_type_class.id,
            }
        )
        cls.env["pms.room"].create(
            {
                "pms_property_id": cls.test_property.id,
                "name": "101",
                "room_type_id": cls.room_type.id,
                "capacity": 2,
            }
        )
        cls.sale_channel = cls.env["pms.sale.channel"].create(
            {"name": "Direct Test", "channel_type": "direct"}
        )
        simplified = cls.env["account.journal"].create(
            {
                "name": "Simplified Sales",
                "code": "SIMP",
                "type": "sale",
                "company_id": company.id,
                "pms_property_ids": [(6, 0, [cls.test_property.id])],
            }
        )
        cls.test_property.write({"journal_simplified_invoice_id": simplified.id})
        cls.transfer_journal = cls.env["account.journal"].create(
            {
                "name": "Balance transfers",
                "code": "TRF",
                "type": "general",
                "company_id": company.id,
            }
        )
        cls.bank_journal = cls.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", company.id)], limit=1
        )
        cls.guest = cls.env["res.partner"].create(
            {"firstname": "John", "lastname": "Doe"}
        )
        cls.customer = cls.env["res.partner"].create(
            {"firstname": "Bill", "lastname": "Payer", "vat": "ES12345678Z"}
        )
        # The invoicing API supports taxes included in the price only.
        sale_tax = cls.env["account.tax"].search(
            [("type_tax_use", "=", "sale"), ("company_id", "=", company.id)],
            limit=1,
        )
        sale_tax.price_include = True
        cls.room_type.product_id.taxes_id = [Command.set(sale_tax.ids)]

    def setUp(self):
        super().setUp()
        # The company opts in by having a transfer journal, which
        # pms_downpayment_lifecycle configures in production.
        patcher = patch.object(
            type(self.env["res.company"]),
            "_get_rectification_transfer_journal",
            lambda company: self.transfer_journal,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    # -- helpers ---------------------------------------------------------
    def _room_line(self):
        start = fields.date.today()
        folio = self.env["pms.folio"].create(
            {
                "pms_property_id": self.test_property.id,
                "partner_name": self.guest.name,
                "partner_id": self.guest.id,
            }
        )
        self.env["pms.reservation"].create(
            {
                "folio_id": folio.id,
                "room_type_id": self.room_type.id,
                "partner_id": self.guest.id,
                "adults": 1,
                "sale_channel_origin_id": self.sale_channel.id,
                "reservation_line_ids": [
                    (0, False, {"date": start + datetime.timedelta(days=i)})
                    for i in range(2)
                ],
            }
        )
        folio.action_confirm()
        folio.reservation_ids.reservation_line_ids.write({"price": 100.0})
        return folio.sale_line_ids.filtered(
            lambda line: not line.display_type and not line.is_downpayment
        )[:1]

    def _validated_invoice(self, test_client, line):
        response = test_client.post(
            "/folios/invoices",
            json={
                "validate": True,
                "customerId": self.customer.id,
                "lines": [
                    {
                        "saleLineId": line.id,
                        "description": "Room nights",
                        "quantityToInvoice": line.qty_to_invoice,
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.text)
        invoice = self.env["account.move"].browse(response.json()["id"])
        self.assertEqual(invoice.state, "posted")
        return invoice

    def _pay(self, invoice):
        payment = self.env["account.payment"].create(
            {
                "payment_type": "inbound",
                "partner_type": "customer",
                "partner_id": invoice.partner_id.id,
                "amount": invoice.amount_total,
                "date": invoice.date,
                "journal_id": self.bank_journal.id,
            }
        )
        payment.action_post()
        (invoice.line_ids + payment.move_id.line_ids).filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
            and not line.reconciled
        ).reconcile()
        self.assertEqual(invoice.payment_state, "paid", "fixture not paid")
        return payment

    def _edit(self, test_client, invoice, line):
        return test_client.put(
            f"/invoices/{invoice.id}?confirmRefund=true",
            json={
                "partner": self.customer.id,
                "invoiceDate": None,
                "invoiceDateDue": None,
                "narration": "",
                "folioLines": [{"id": line.id, "quantity": 2, "description": "Edited"}],
                "downpaymentLines": [],
            },
        )

    def _transfers(self):
        return self.env["account.move"].search(
            [("journal_id", "=", self.transfer_journal.id)]
        )

    # -- tests -----------------------------------------------------------
    def test_editing_a_paid_invoice_keeps_its_payment(self):
        line = self._room_line()
        with self._create_test_client() as test_client:
            self._login(test_client)
            original = self._validated_invoice(test_client, line)
            payment = self._pay(original)

            response = self._edit(test_client, original, line)
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            replacement = self.env["account.move"].browse(response.json()["id"])

            # Rectified, and the payment never moved.
            self.assertEqual(replacement.state, "draft")
            self.assertEqual(replacement.replaces_invoice_id, original)
            self.assertEqual(original.payment_state, "paid")
            self.assertTrue(payment.is_reconciled)
            self.assertTrue(original._is_rectified())
            credit_note = original.reversal_move_id
            self.assertEqual(credit_note.amount_residual, original.amount_total)

            validate = test_client.post(f"/invoices/{replacement.id}/validate")
            self.assertEqual(validate.status_code, status.HTTP_200_OK, validate.text)

        # Validated, the replacement takes the money over.
        self.assertEqual(replacement.payment_state, "paid")
        self.assertEqual(credit_note.amount_residual, 0.0)
        self.assertEqual(len(self._transfers()), 1)
        self.assertTrue(payment.is_reconciled)

    def test_an_unpaid_invoice_is_still_reversed(self):
        line = self._room_line()
        with self._create_test_client() as test_client:
            self._login(test_client)
            original = self._validated_invoice(test_client, line)
            response = self._edit(test_client, original, line)
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            replacement = self.env["account.move"].browse(response.json()["id"])
            validate = test_client.post(f"/invoices/{replacement.id}/validate")
            self.assertEqual(validate.status_code, status.HTTP_200_OK, validate.text)
        self.assertEqual(original.payment_state, "reversed")
        self.assertFalse(self._transfers())

    def test_a_rectified_invoice_cannot_be_edited_again(self):
        line = self._room_line()
        with self._create_test_client() as test_client:
            self._login(test_client)
            original = self._validated_invoice(test_client, line)
            self._pay(original)
            self.assertEqual(
                self._edit(test_client, original, line).status_code,
                status.HTTP_200_OK,
            )
            response = self._edit(test_client, original, line)
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, response.text)
        self.assertEqual(response.json()["type"], "/errors/invoice-not-editable")

    def test_the_payment_of_a_rectified_invoice_cannot_be_removed(self):
        """Its money already pays the replacement: removed, the rectified
        invoice would owe it again."""
        line = self._room_line()
        with self._create_test_client() as test_client:
            self._login(test_client)
            original = self._validated_invoice(test_client, line)
            payment = self._pay(original)
            self.assertEqual(
                self._edit(test_client, original, line).status_code,
                status.HTTP_200_OK,
            )
            response = test_client.delete(
                f"/invoices/{original.id}/reconciliations/payment_{payment.id}"
            )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, response.text)
        self.assertTrue(payment.is_reconciled)
        self.assertEqual(original.payment_state, "paid")

    def test_the_draft_replacement_cannot_be_deleted(self):
        """It is the only document that can take over the money its original's
        credit note holds."""
        line = self._room_line()
        with self._create_test_client() as test_client:
            self._login(test_client)
            original = self._validated_invoice(test_client, line)
            self._pay(original)
            response = self._edit(test_client, original, line)
            replacement = self.env["account.move"].browse(response.json()["id"])
            # The test client lets the UserError through; served, it is a 400.
            with self.assertRaisesRegex(UserError, "instead of deleting"):
                test_client.delete(f"/invoices/{replacement.id}")
        self.assertTrue(replacement.exists())
        self.assertEqual(
            original.reversal_move_id.amount_residual, original.amount_total
        )

    def test_a_replacement_in_instalments_is_not_validated(self):
        """With several customer lines, where to put the money is ambiguous:
        validated anyway, it stayed unpaid with nothing saying why."""
        line = self._room_line()
        with self._create_test_client() as test_client:
            self._login(test_client)
            original = self._validated_invoice(test_client, line)
            self._pay(original)
            response = self._edit(test_client, original, line)
            replacement = self.env["account.move"].browse(response.json()["id"])
            replacement.invoice_payment_term_id = self.env.ref(
                "account.account_payment_term_advance_60days"
            )
            validate = test_client.post(f"/invoices/{replacement.id}/validate")
        self.assertEqual(validate.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(replacement.state, "draft")
