from unittest.mock import patch

from fastapi import status

from odoo import Command
from odoo.tests import tagged

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi

_RENDER_QWEB_PDF = (
    "odoo.addons.base.models.ir_actions_report.IrActionsReport._render_qweb_pdf"
)
_MAIL_SEND = "odoo.addons.mail.models.mail_mail.MailMail.send"


@tagged("post_install", "-at_install")
class TestInvoiceEmailsEndpoints(CommonTestPmsApi):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.test_company
        if not company.chart_template_id:
            coa = cls.env.ref("l10n_generic_coa.configurable_chart_template", False)
            if not coa:
                coa = cls.env["account.chart.template"].search(
                    [("visible", "=", True)], limit=1
                )
            if not coa:
                cls.skipTest(cls, "No chart of accounts available.")
            coa.try_loading(company=company, install_demo=False)
        cls.env.user.write(
            {
                "company_ids": [Command.link(company.id)],
                "company_id": company.id,
            }
        )
        cls.env = cls.env(
            context=dict(cls.env.context, allowed_company_ids=company.ids)
        )
        cls.journal_sale = cls.env["account.journal"].search(
            [("type", "=", "sale"), ("company_id", "=", company.id)], limit=1
        )
        cls.partner = cls.env["res.partner"].create(
            {"name": "Test Invoice Partner", "email": "customer@example.org"}
        )

    def _create_invoice(self, move_type="out_invoice", amount=100.0):
        invoice = self.env["account.move"].create(
            {
                "move_type": move_type,
                "partner_id": self.partner.id,
                "pms_property_id": self.test_property.id,
                "journal_id": self.journal_sale.id,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": "Test line",
                            "quantity": 1,
                            "price_unit": amount,
                            "tax_ids": [Command.clear()],
                        }
                    )
                ],
            }
        )
        invoice.action_post()
        return invoice

    def _invoice_mails(self, invoice):
        return self.env["mail.mail"].search(
            [("model", "=", "account.move"), ("res_id", "=", invoice.id)]
        )

    # -- GET /invoices/{id}/email-template --

    def test_email_template_ok(self):
        """GET returns a rendered subject and HTML body."""
        invoice = self._create_invoice()
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(f"/invoices/{invoice.id}/email-template")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        data = response.json()
        self.assertTrue(data["subject"])
        self.assertIn("body", data)
        self.assertIsInstance(data["body"], str)

    def test_email_template_explicit_lang(self):
        """GET honours an active language passed as query param."""
        invoice = self._create_invoice()
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(
                f"/invoices/{invoice.id}/email-template?lang=en_US"
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)

    def test_email_template_invalid_lang(self):
        """GET with a language not configured in the instance returns 422."""
        invoice = self._create_invoice()
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(
                f"/invoices/{invoice.id}/email-template?lang=zz_ZZ"
            )
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        self.assertEqual(response.json()["type"], "/errors/invalid-language")

    def test_email_template_not_found(self):
        """GET returns 404 for a non-existent invoice."""
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get("/invoices/999999999/email-template")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)
        self.assertEqual(response.json()["type"], "/errors/not-found")

    # -- POST /invoices/{id}/emails --

    def test_send_email_success(self):
        """POST sends a single mail with the PDF attached to both a contact and
        a free address, and returns 204."""
        invoice = self._create_invoice()
        with patch(_RENDER_QWEB_PDF, return_value=(b"%PDF-1.4 fake", "pdf")), patch(
            _MAIL_SEND, return_value=True
        ):
            with self._create_test_client() as test_client:
                self._login(test_client)
                response = test_client.post(
                    f"/invoices/{invoice.id}/emails",
                    json={
                        "contactIds": [self.partner.id],
                        "emailAddresses": ["free@example.org"],
                        "subject": "Final subject",
                        "body": "<p>Final body</p>",
                    },
                )
        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )
        mails = self._invoice_mails(invoice)
        self.assertEqual(len(mails), 1)
        mail = mails
        self.assertEqual(mail.subject, "Final subject")
        self.assertIn(self.partner, mail.recipient_ids)
        self.assertIn("free@example.org", mail.email_to)
        self.assertTrue(mail.attachment_ids)

    def test_send_email_no_recipients(self):
        """POST with both recipient lists empty returns 422."""
        invoice = self._create_invoice()
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.post(
                f"/invoices/{invoice.id}/emails",
                json={
                    "contactIds": [],
                    "emailAddresses": [],
                    "subject": "S",
                    "body": "<p>B</p>",
                },
            )
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        self.assertEqual(response.json()["type"], "/errors/no-recipients")
        self.assertFalse(self._invoice_mails(invoice))

    def test_send_email_invalid_address(self):
        """POST with a malformed email address returns 422."""
        invoice = self._create_invoice()
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.post(
                f"/invoices/{invoice.id}/emails",
                json={
                    "contactIds": [],
                    "emailAddresses": ["not-an-email"],
                    "subject": "S",
                    "body": "<p>B</p>",
                },
            )
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        self.assertEqual(response.json()["type"], "/errors/invalid-email")

    def test_send_email_unknown_contact(self):
        """POST with a non-existent contact id returns 422."""
        invoice = self._create_invoice()
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.post(
                f"/invoices/{invoice.id}/emails",
                json={
                    "contactIds": [999999999],
                    "emailAddresses": [],
                    "subject": "S",
                    "body": "<p>B</p>",
                },
            )
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        self.assertEqual(response.json()["type"], "/errors/contact-not-found")

    def test_send_email_not_found(self):
        """POST returns 404 for a non-existent invoice."""
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.post(
                "/invoices/999999999/emails",
                json={
                    "contactIds": [],
                    "emailAddresses": ["free@example.org"],
                    "subject": "S",
                    "body": "<p>B</p>",
                },
            )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)
        self.assertEqual(response.json()["type"], "/errors/not-found")

    def test_send_email_delivery_failure_rolls_back(self):
        """A delivery failure returns 502 and leaves no mail behind."""
        invoice = self._create_invoice()
        with patch(_RENDER_QWEB_PDF, return_value=(b"%PDF-1.4 fake", "pdf")), patch(
            _MAIL_SEND, side_effect=Exception("smtp down")
        ):
            with self._create_test_client() as test_client:
                self._login(test_client)
                response = test_client.post(
                    f"/invoices/{invoice.id}/emails",
                    json={
                        "contactIds": [self.partner.id],
                        "emailAddresses": [],
                        "subject": "S",
                        "body": "<p>B</p>",
                    },
                )
        self.assertEqual(
            response.status_code, status.HTTP_502_BAD_GATEWAY, response.text
        )
        self.assertEqual(response.json()["type"], "/errors/email-delivery-failed")
        self.assertFalse(self._invoice_mails(invoice))
