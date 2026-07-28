import datetime
import warnings
from functools import partial

import jwt
from fastapi import status
from requests import Response

from odoo import Command, fields

from odoo.addons.extendable_fastapi.tests.common import FastAPITransactionCase
from odoo.addons.fastapi.dependencies import fastapi_endpoint


class CommonTestPmsApi(FastAPITransactionCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        warnings.filterwarnings("ignore", category=jwt.InsecureKeyLengthWarning)

        jwt_validator = cls.env["auth.jwt.validator"].search([("name", "=", "api_pms")])
        jwt_validator.cookie_secure = False
        cls.pms_fastapi_app = cls.env["fastapi.endpoint"].create(
            {
                "name": "PMS FastAPI",
                "app": "pms_api",
                "root_path": "/pmsApi",
                "user_id": cls.env.ref("pms_fastapi.pms_fastapi_user").id,
            }
        )
        cls.env = cls.env(context=dict(cls.env.context, queue_job__no_delay=True))
        cls.default_fastapi_app = cls.pms_fastapi_app._get_app()
        cls.default_fastapi_dependency_overrides = {
            fastapi_endpoint: partial(lambda a: a, cls.pms_fastapi_app)
        }
        cls.default_fastapi_odoo_env = cls.env
        cls.default_fastapi_running_user = cls.pms_fastapi_app.user_id
        cls.test_user = cls.env["res.users"].create(
            {
                "name": "PMS api test",
                "login": "test_pms_api",
                "password": "supersecret",
                "email": "test@example.org",
            }
        )
        cls.test_availability_plan = cls.env["pms.availability.plan"].create(
            {"name": "Availability Plan 1"}
        )
        cls.test_pricelist = cls.env["product.pricelist"].create(
            {
                "name": "Pricelist 1",
                "availability_plan_id": cls.test_availability_plan.id,
            }
        )
        cls.test_company = cls.env["res.company"].create(
            {
                "name": "Company 1",
                "vat": "ES11111111H",
            }
        )
        cls.test_property = cls.env["pms.property"].create(
            {
                "name": "Property 1",
                "company_id": cls.test_company.id,
                "default_pricelist_id": cls.test_pricelist.id,
                "user_ids": [(6, 0, [cls.test_user.id])],
            }
        )
        cls.test_user.write({"company_ids": [(4, cls.test_company.id)]})

    def _login(self, test_client, password="supersecret"):
        response: Response = test_client.post(
            "/login",
            json={
                "username": "test_pms_api",
                "password": password,
            },
        )
        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )
        return response


class CommonTestPmsApiPayment(CommonTestPmsApi):
    """Accounting fixture shared by the payment test suites.

    The payments ledger (/payments) and the three payment entities
    (/customer-payments, /supplier-payments, /internal-transfers) all need the
    same setup: a chart of accounts, journals tied to the property with their
    payment method lines, and a folio that can hold a real invoiceable
    reservation.
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
        # Journals tied to the property: only these are in API scope.
        cls.journal_bank = cls.env["account.journal"].create(
            {
                "name": "Bank PMS",
                "type": "bank",
                "code": "BNKP",
                "company_id": company.id,
                "pms_property_ids": [Command.set([cls.test_property.id])],
            }
        )
        cls.journal_bank2 = cls.env["account.journal"].create(
            {
                "name": "Bank PMS 2",
                "type": "bank",
                "code": "BNKP2",
                "company_id": company.id,
                "pms_property_ids": [Command.set([cls.test_property.id])],
            }
        )
        # Profit/loss accounts so a cash session can be closed (which is what
        # matches its payments against the bank in this PMS).
        income_account = cls.env["account.account"].search(
            [("company_id", "=", company.id), ("account_type", "=", "income_other")],
            limit=1,
        ) or cls.env["account.account"].search(
            [("company_id", "=", company.id), ("account_type", "=", "income")], limit=1
        )
        expense_account = cls.env["account.account"].search(
            [("company_id", "=", company.id), ("account_type", "=", "expense")], limit=1
        )
        cls.journal_cash = cls.env["account.journal"].create(
            {
                "name": "Cash Reception",
                "type": "cash",
                "code": "CSHP",
                "company_id": company.id,
                "pms_property_ids": [Command.set([cls.test_property.id])],
                "profit_account_id": income_account.id,
                "loss_account_id": expense_account.id,
            }
        )
        # Journal NOT tied to any property: must stay out of scope.
        cls.journal_no_property = cls.env["account.journal"].create(
            {
                "name": "Bank no property",
                "type": "bank",
                "code": "BNKNP",
                "company_id": company.id,
            }
        )
        cls.bank_inbound = cls.journal_bank.inbound_payment_method_line_ids[:1]
        cls.bank_outbound = cls.journal_bank.outbound_payment_method_line_ids[:1]
        cls.bank2_inbound = cls.journal_bank2.inbound_payment_method_line_ids[:1]
        cls.bank2_outbound = cls.journal_bank2.outbound_payment_method_line_ids[:1]
        cls.cash_inbound = cls.journal_cash.inbound_payment_method_line_ids[:1]
        cls.cash_outbound = cls.journal_cash.outbound_payment_method_line_ids[:1]
        cls.customer = cls.env["res.partner"].create({"name": "Pay Customer"})
        cls.supplier = cls.env["res.partner"].create({"name": "Pay Supplier"})
        # Room infrastructure so a folio can hold a real (invoiceable)
        # reservation line instead of a hand-crafted folio.sale.line.
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
        # Folio invoicing with no explicit partner picks the property's
        # simplified sale journal; without it _create_invoices() raises.
        cls.journal_simplified = cls.env["account.journal"].create(
            {
                "name": "Simplified Sales",
                "code": "SIMP",
                "type": "sale",
                "company_id": company.id,
                "pms_property_ids": [Command.set([cls.test_property.id])],
            }
        )
        cls.test_property.write(
            {"journal_simplified_invoice_id": cls.journal_simplified.id}
        )

    def _create_payment(
        self,
        amount=100.0,
        payment_type="inbound",
        partner_type="customer",
        journal=None,
        partner=None,
        ref="",
        pay_date=None,
        is_internal_transfer=False,
        destination_journal=None,
        post=True,
    ):
        """A payment created straight through the ORM, to set up a scenario
        without going through the API."""
        vals = {
            "amount": amount,
            "payment_type": payment_type,
            "partner_type": partner_type,
            "journal_id": (journal or self.journal_bank).id,
            "partner_id": (partner or self.customer).id,
            "ref": ref,
            "date": pay_date or datetime.date(2025, 12, 22),
            "is_internal_transfer": is_internal_transfer,
        }
        if is_internal_transfer:
            vals["destination_journal_id"] = (
                destination_journal or self.journal_bank2
            ).id
        payment = self.env["account.payment"].create(vals)
        if post:
            payment.action_post()
        return payment

    def _folio(self):
        return self.env["pms.folio"].create(
            {
                "pms_property_id": self.test_property.id,
                "partner_name": self.customer.name,
                "partner_id": self.customer.id,
                "pricelist_id": self.test_pricelist.id,
            }
        )

    def _confirmed_folio(self, nights=2, price=100.0):
        """A folio with a confirmed, invoiceable reservation."""
        start = fields.date.today()
        folio = self._folio()
        self.env["pms.reservation"].create(
            {
                "folio_id": folio.id,
                "room_type_id": self.room_type.id,
                "partner_id": self.customer.id,
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

    def _invoice_for_folio(self, folio, post=True):
        """Invoice the folio the way the PMS does (real sale lines), so the
        folio<->invoice link is genuine."""
        invoice = folio._create_invoices()[:1]
        if post:
            invoice.action_post()
        return invoice
