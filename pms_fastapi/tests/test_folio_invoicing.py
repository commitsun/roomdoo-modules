import datetime
from unittest.mock import patch

from fastapi import status

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi


@tagged("post_install", "-at_install")
class TestFolioInvoicing(CommonTestPmsApi):
    """POST /folios/invoices (create) and PUT /invoices/{id} (edit).

    Exercises the module's invoicing orchestration: sale-line resolution,
    validation branches (RFC 9457 problems), draft rewrite and posted-invoice
    refund flow.
    """

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

        cls.room_type_class = cls.env["pms.room.type.class"].create(
            {"name": "Standard", "default_code": "STD"}
        )
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.test_property.id],
                "name": "Double Test",
                "default_code": "DBL_Test",
                "class_id": cls.room_type_class.id,
            }
        )
        cls.room1 = cls.env["pms.room"].create(
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
        # Simplified invoices need a dedicated sale journal; linking it as the
        # property's simplified journal flags it as such (is_simplified_invoice).
        cls.journal_simplified = cls.env["account.journal"].create(
            {
                "name": "Simplified Sales",
                "code": "SIMP",
                "type": "sale",
                "company_id": company.id,
                "pms_property_ids": [(6, 0, [cls.test_property.id])],
            }
        )
        cls.test_property.write(
            {"journal_simplified_invoice_id": cls.journal_simplified.id}
        )
        cls.guest = cls.env["res.partner"].create(
            {"firstname": "John", "lastname": "Doe"}
        )
        cls.customer = cls.env["res.partner"].create(
            {"firstname": "Bill", "lastname": "Payer", "vat": "ES12345678Z"}
        )
        cls.customer_no_id = cls.env["res.partner"].create(
            {"firstname": "No", "lastname": "Fiscal"}
        )

    # -- helpers -------------------------------------------------------
    def _confirmed_folio(self, nights=2, days_ahead=0, price=100.0):
        start = fields.date.today() + datetime.timedelta(days=days_ahead)
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
                    for i in range(nights)
                ],
            }
        )
        folio.action_confirm()
        folio.reservation_ids.reservation_line_ids.write({"price": price})
        return folio

    def _room_line(self, folio):
        return folio.sale_line_ids.filtered(
            lambda line: not line.display_type and not line.is_downpayment
        )[:1]

    def _create_payload(self, line, qty=None, customer_id=None, validate=False):
        return {
            "validate": validate,
            "customerId": customer_id,
            "lines": [
                {
                    "saleLineId": line.id,
                    "description": "Room nights",
                    "quantityToInvoice": qty
                    if qty is not None
                    else line.qty_to_invoice,
                }
            ],
        }

    def _downpayment_invoice(self, folio, amount=50.0):
        # Mirror the structure the down-payment wizard produces: an invoice
        # tied to the folio whose line points at a down-payment folio.sale.line.
        # This is what account.move._is_downpayment() keys off.
        product = self.room_type.product_id
        line = self.env["folio.sale.line"].create(
            {
                "folio_id": folio.id,
                "name": "Down payment",
                "is_downpayment": True,
                "product_id": product.id,
                "product_uom": product.uom_id.id,
                "product_uom_qty": 1,
                "price_unit": amount,
            }
        )
        return self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.customer.id,
                "folio_ids": [(6, 0, folio.ids)],
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Down payment",
                            "product_id": product.id,
                            "quantity": 1,
                            "price_unit": amount,
                            "folio_line_ids": [(6, 0, line.ids)],
                        },
                    )
                ],
            }
        )

    def _post_invoice(self, test_client, payload):
        return test_client.post("/folios/invoices", json=payload)

    # ------------------------------------------------------------------
    # POST /folios/invoices — happy paths
    # ------------------------------------------------------------------
    def test_create_invoice_draft_with_customer(self):
        folio = self._confirmed_folio()
        line = self._room_line(folio)
        self.assertGreater(line.qty_to_invoice, 0)
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = self._post_invoice(
                test_client,
                self._create_payload(line, customer_id=self.customer.id),
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.text)
        data = response.json()
        self.assertEqual(data["state"], "draft")
        self.assertIn(folio.id, [f["id"] for f in data["folios"]])

    def test_create_invoice_posted_when_validate_true(self):
        folio = self._confirmed_folio()
        line = self._room_line(folio)
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = self._post_invoice(
                test_client,
                self._create_payload(line, customer_id=self.customer.id, validate=True),
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.text)
        self.assertEqual(response.json()["state"], "posted")

    def test_create_simplified_invoice_without_customer(self):
        folio = self._confirmed_folio()
        line = self._room_line(folio)
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = self._post_invoice(
                test_client, self._create_payload(line, customer_id=None)
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.text)
        self.assertEqual(response.json()["invoiceType"], "outInvoice")

    # ------------------------------------------------------------------
    # POST /folios/invoices — validation branches
    # ------------------------------------------------------------------
    def test_create_invoice_duplicate_sale_lines(self):
        folio = self._confirmed_folio()
        line = self._room_line(folio)
        payload = {
            "validate": False,
            "customerId": self.customer.id,
            "lines": [
                {"saleLineId": line.id, "description": "a", "quantityToInvoice": 1},
                {"saleLineId": line.id, "description": "b", "quantityToInvoice": 1},
            ],
        }
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = self._post_invoice(test_client, payload)
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        self.assertEqual(response.json()["type"], "/errors/duplicate-sale-lines")

    def test_create_invoice_sale_line_not_found(self):
        payload = {
            "validate": False,
            "customerId": self.customer.id,
            "lines": [
                {
                    "saleLineId": 999999999,
                    "description": "x",
                    "quantityToInvoice": 1,
                }
            ],
        }
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = self._post_invoice(test_client, payload)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)
        self.assertEqual(response.json()["type"], "/errors/sale-lines-not-found")

    def test_create_invoice_rejects_section_line(self):
        folio = self._confirmed_folio()
        section = self.env["folio.sale.line"].create(
            {"folio_id": folio.id, "display_type": "line_section", "name": "Section"}
        )
        payload = {
            "validate": False,
            "customerId": self.customer.id,
            "lines": [
                {"saleLineId": section.id, "description": "s", "quantityToInvoice": 1}
            ],
        }
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = self._post_invoice(test_client, payload)
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        self.assertEqual(response.json()["type"], "/errors/invalid-sale-line")

    def test_create_invoice_quantity_exceeds_pending(self):
        folio = self._confirmed_folio()
        line = self._room_line(folio)
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = self._post_invoice(
                test_client,
                self._create_payload(line, qty=999, customer_id=self.customer.id),
            )
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        self.assertEqual(response.json()["type"], "/errors/quantity-exceeds-pending")

    def test_create_invoice_customer_not_found(self):
        folio = self._confirmed_folio()
        line = self._room_line(folio)
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = self._post_invoice(
                test_client, self._create_payload(line, customer_id=999999999)
            )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)

    def test_create_invoice_customer_fails_validation(self):
        folio = self._confirmed_folio()
        line = self._room_line(folio)
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = self._post_invoice(
                test_client,
                self._create_payload(line, customer_id=self.customer_no_id.id),
            )
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        self.assertEqual(response.json()["type"], "/errors/invoicing-validation-failed")

    def test_create_invoice_simplified_limit_exceeded(self):
        folio = self._confirmed_folio()
        line = self._room_line(folio)
        self.test_property.write({"max_amount_simplified_invoice": 1.0})
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = self._post_invoice(
                test_client, self._create_payload(line, customer_id=None)
            )
        self.test_property.write({"max_amount_simplified_invoice": 0.0})
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        self.assertEqual(
            response.json()["type"], "/errors/simplified-invoice-limit-exceeded"
        )

    def test_create_invoice_multiple_properties(self):
        folio_a = self._confirmed_folio()
        line_a = self._room_line(folio_a)

        property_b = self.env["pms.property"].create(
            {
                "name": "Property B",
                "company_id": self.test_company.id,
                "default_pricelist_id": self.test_pricelist.id,
                "user_ids": [(6, 0, [self.test_user.id])],
            }
        )
        self.room_type.write({"pms_property_ids": [(4, property_b.id)]})
        self.env["pms.room"].create(
            {
                "pms_property_id": property_b.id,
                "name": "201",
                "room_type_id": self.room_type.id,
                "capacity": 2,
            }
        )
        folio_b = self.env["pms.folio"].create(
            {
                "pms_property_id": property_b.id,
                "partner_name": self.guest.name,
                "partner_id": self.guest.id,
            }
        )
        self.env["pms.reservation"].create(
            {
                "folio_id": folio_b.id,
                "room_type_id": self.room_type.id,
                "partner_id": self.guest.id,
                "adults": 1,
                "sale_channel_origin_id": self.sale_channel.id,
                "reservation_line_ids": [(0, False, {"date": fields.date.today()})],
            }
        )
        folio_b.action_confirm()
        folio_b.reservation_ids.reservation_line_ids.write({"price": 100.0})
        line_b = self._room_line(folio_b)

        payload = {
            "validate": False,
            "customerId": self.customer.id,
            "lines": [
                {"saleLineId": line_a.id, "description": "a", "quantityToInvoice": 1},
                {"saleLineId": line_b.id, "description": "b", "quantityToInvoice": 1},
            ],
        }
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = self._post_invoice(test_client, payload)
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        self.assertEqual(response.json()["type"], "/errors/multiple-properties")

    def test_create_invoice_rolls_back_on_move_error(self):
        # A receivable account with a wrong account_type makes the core
        # constraint _check_payable_receivable raise while account.move is
        # being created. Odoo constraints run after the INSERT, so without a
        # savepoint around the attempt the move would get committed even
        # though the endpoint returns an error. The request must fail AND
        # leave no orphan move behind.
        folio = self._confirmed_folio()
        line = self._room_line(folio)
        bad_account = self.env["account.account"].create(
            {
                "name": "Wrong Receivable",
                "code": "TESTBADRCV",
                "account_type": "asset_current",
                "company_id": self.test_company.id,
            }
        )
        self.customer.with_company(
            self.test_company
        ).property_account_receivable_id = bad_account
        move_domain = [("folio_ids", "in", folio.ids)]
        self.assertEqual(self.env["account.move"].search_count(move_domain), 0)
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = self._post_invoice(
                test_client,
                self._create_payload(line, customer_id=self.customer.id),
            )
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        self.assertEqual(
            self.env["account.move"].search_count(move_domain),
            0,
            "A failed invoice creation must not leave an orphan move.",
        )

    # ------------------------------------------------------------------
    # POST /folios/invoices — downpaymentLines
    # ------------------------------------------------------------------
    def test_create_invoice_with_downpayment_invoice(self):
        # A valid down-payment invoice passes resolution/validation and reaches
        # invoice creation. The actual deduction is folio invoicing machinery
        # (pms), not asserted here.
        folio = self._confirmed_folio()
        line = self._room_line(folio)
        downpayment = self._downpayment_invoice(folio)
        payload = self._create_payload(line, customer_id=self.customer.id)
        payload["downpaymentLines"] = [downpayment.id]
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = self._post_invoice(test_client, payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.text)
        self.assertEqual(response.json()["state"], "draft")

    def test_create_invoice_downpayment_not_found(self):
        folio = self._confirmed_folio()
        line = self._room_line(folio)
        payload = self._create_payload(line, customer_id=self.customer.id)
        payload["downpaymentLines"] = [999999999]
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = self._post_invoice(test_client, payload)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)
        self.assertEqual(response.json()["type"], "/errors/downpayment-lines-not-found")

    def test_create_invoice_rejects_non_downpayment_as_downpayment(self):
        folio = self._confirmed_folio()
        line = self._room_line(folio)
        # A regular invoice (no down-payment lines) is not a down payment.
        regular_invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.customer.id,
                "folio_ids": [(6, 0, folio.ids)],
            }
        )
        payload = self._create_payload(line, customer_id=self.customer.id)
        payload["downpaymentLines"] = [regular_invoice.id]
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = self._post_invoice(test_client, payload)
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        self.assertEqual(response.json()["type"], "/errors/invalid-downpayment-line")

    def test_create_invoice_downpayment_out_of_scope(self):
        folio = self._confirmed_folio()
        line = self._room_line(folio)
        # Different dates so the single test room stays available.
        other_folio = self._confirmed_folio(days_ahead=30)
        downpayment = self._downpayment_invoice(other_folio)
        payload = self._create_payload(line, customer_id=self.customer.id)
        payload["downpaymentLines"] = [downpayment.id]
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = self._post_invoice(test_client, payload)
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        self.assertEqual(
            response.json()["type"], "/errors/downpayment-line-out-of-scope"
        )

    def test_create_invoice_duplicate_downpayment_lines(self):
        folio = self._confirmed_folio()
        line = self._room_line(folio)
        downpayment = self._downpayment_invoice(folio)
        payload = self._create_payload(line, customer_id=self.customer.id)
        payload["downpaymentLines"] = [downpayment.id, downpayment.id]
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = self._post_invoice(test_client, payload)
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        self.assertEqual(response.json()["type"], "/errors/duplicate-downpayment-lines")

    # ------------------------------------------------------------------
    # PUT /invoices/{id} — edit
    # ------------------------------------------------------------------
    def _create_draft_invoice(self, test_client, folio, qty=1):
        line = self._room_line(folio)
        response = self._post_invoice(
            test_client,
            self._create_payload(line, qty=qty, customer_id=self.customer.id),
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.text)
        return response.json()["id"], line

    def _edit_payload(self, line, quantity=1, partner=None):
        return {
            "partner": (partner or self.customer).id,
            "invoiceDate": None,
            "invoiceDateDue": None,
            "narration": "",
            "folioLines": [
                {"id": line.id, "quantity": quantity, "description": "Edited"}
            ],
            "downpaymentLines": [],
        }

    def test_edit_invoice_not_found(self):
        folio = self._confirmed_folio()
        line = self._room_line(folio)
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.put(
                "/invoices/999999999", json=self._edit_payload(line)
            )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)

    def test_edit_draft_invoice_rewrites_in_place(self):
        folio = self._confirmed_folio()
        with self._create_test_client() as test_client:
            self._login(test_client)
            invoice_id, line = self._create_draft_invoice(test_client, folio, qty=2)
            response = test_client.put(
                f"/invoices/{invoice_id}",
                json=self._edit_payload(line, quantity=1),
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        data = response.json()
        self.assertEqual(data["id"], invoice_id)
        self.assertEqual(data["state"], "draft")
        folio_line = next(fl for fl in data["folioLines"] if fl["id"] == line.id)
        self.assertEqual(folio_line["quantity"], 1)

    def test_edit_posted_invoice_requires_refund_confirmation(self):
        folio = self._confirmed_folio()
        with self._create_test_client() as test_client:
            self._login(test_client)
            invoice_id, line = self._create_draft_invoice(test_client, folio, qty=1)
            validate = test_client.post(f"/invoices/{invoice_id}/validate")
            self.assertEqual(validate.status_code, status.HTTP_200_OK, validate.text)
            response = test_client.put(
                f"/invoices/{invoice_id}", json=self._edit_payload(line, quantity=1)
            )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, response.text)
        self.assertEqual(
            response.json()["type"],
            "/errors/invoice-refund-confirmation-required",
        )

    def test_edit_posted_invoice_with_confirm_refund_creates_replacement(self):
        folio = self._confirmed_folio()
        with self._create_test_client() as test_client:
            self._login(test_client)
            invoice_id, line = self._create_draft_invoice(test_client, folio, qty=1)
            test_client.post(f"/invoices/{invoice_id}/validate")
            response = test_client.put(
                f"/invoices/{invoice_id}?confirmRefund=true",
                json=self._edit_payload(line, quantity=1),
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        data = response.json()
        self.assertNotEqual(data["id"], invoice_id)
        self.assertIsNotNone(data["replaces"])
        self.assertEqual(data["replaces"]["id"], invoice_id)

    def test_edit_invoice_composition_already_invoiced(self):
        folio = self._confirmed_folio()
        with self._create_test_client() as test_client:
            self._login(test_client)
            # Two drafts share the same sale line (partial invoices).
            _first_id, line = self._create_draft_invoice(test_client, folio, qty=1)
            second_id, _line = self._create_draft_invoice(test_client, folio, qty=1)
            response = test_client.put(
                f"/invoices/{second_id}", json=self._edit_payload(line, quantity=1)
            )
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        self.assertEqual(response.json()["type"], "/errors/invoice-composition-invalid")

    def test_edit_invoice_partner_fails_validation(self):
        folio = self._confirmed_folio()
        with self._create_test_client() as test_client:
            self._login(test_client)
            invoice_id, line = self._create_draft_invoice(test_client, folio, qty=1)
            response = test_client.put(
                f"/invoices/{invoice_id}",
                json=self._edit_payload(line, partner=self.customer_no_id),
            )
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        self.assertEqual(response.json()["type"], "/errors/invoicing-validation-failed")

    # ------------------------------------------------------------------
    # Transaction safety — a failure after a mutation must roll back
    # ------------------------------------------------------------------
    def test_edit_draft_invoice_rolls_back_on_failure(self):
        # _edit_draft_invoice unlinks the draft's lines and then rebuilds them.
        # If the rebuild step fails, the savepoint must restore the lines
        # instead of leaving the draft emptied.
        folio = self._confirmed_folio()
        helper_cls = type(self.env["pms_api_invoice.invoice_router.helper"])

        def _boom(helper, *args, **kwargs):
            helper._raise_problem(422, "/errors/injected-failure", "Injected", "boom")

        with self._create_test_client() as test_client:
            self._login(test_client)
            invoice_id, line = self._create_draft_invoice(test_client, folio, qty=2)
            with patch.object(helper_cls, "_edit_compute_invoice_vals", _boom):
                response = test_client.put(
                    f"/invoices/{invoice_id}",
                    json=self._edit_payload(line, quantity=1),
                )
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        invoice = self.env["account.move"].browse(invoice_id)
        self.assertTrue(
            invoice.invoice_line_ids,
            "A rolled-back edit must leave the draft's lines intact.",
        )

    def test_edit_posted_invoice_rolls_back_on_failure(self):
        # _edit_posted_invoice reverses the invoice (posted credit note) before
        # rebuilding. If the rebuild fails, the savepoint must roll back so no
        # orphan credit note is committed and the original stays posted.
        folio = self._confirmed_folio()
        helper_cls = type(self.env["pms_api_invoice.invoice_router.helper"])

        def _boom(helper, *args, **kwargs):
            helper._raise_problem(422, "/errors/injected-failure", "Injected", "boom")

        with self._create_test_client() as test_client:
            self._login(test_client)
            invoice_id, line = self._create_draft_invoice(test_client, folio, qty=1)
            self.assertEqual(
                test_client.post(f"/invoices/{invoice_id}/validate").status_code,
                status.HTTP_200_OK,
            )
            moves_before = self.env["account.move"].search_count(
                [("company_id", "=", self.test_company.id)]
            )
            with patch.object(helper_cls, "_edit_compute_invoice_vals", _boom):
                response = test_client.put(
                    f"/invoices/{invoice_id}?confirmRefund=true",
                    json=self._edit_payload(line, quantity=1),
                )
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        invoice = self.env["account.move"].browse(invoice_id)
        self.assertEqual(invoice.state, "posted")
        self.assertEqual(
            self.env["account.move"].search_count(
                [("company_id", "=", self.test_company.id)]
            ),
            moves_before,
            "The reversal credit note must be rolled back, leaving no orphan move.",
        )

    def test_validate_invoice_rolls_back_on_post_failure(self):
        # If action_post mutates and then raises, the savepoint must roll back
        # so the invoice is not left partially posted.
        folio = self._confirmed_folio()
        move_cls = type(self.env["account.move"])
        marker = "ROLLED_BACK_MARKER"

        def _boom(moves, *args, **kwargs):
            moves.write({"narration": marker})
            raise UserError("boom")

        with self._create_test_client() as test_client:
            self._login(test_client)
            invoice_id, _line = self._create_draft_invoice(test_client, folio, qty=1)
            with patch.object(move_cls, "action_post", _boom):
                response = test_client.post(f"/invoices/{invoice_id}/validate")
        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.text
        )
        invoice = self.env["account.move"].browse(invoice_id)
        self.assertEqual(invoice.state, "draft")
        self.assertNotEqual(
            invoice.narration or "",
            marker,
            "The write done before the post failure must be rolled back.",
        )
