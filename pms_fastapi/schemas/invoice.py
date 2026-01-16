from datetime import date
from enum import Enum
from typing import Annotated

from fastapi import Query
from pydantic import Field

from odoo import api
from odoo.osv import expression

from .base import BaseSearch, PmsBaseModel
from .contact import ContactId
from .currency import CurrencySummary


class InvoiceOrderField(str, Enum):
    name = "name"
    invoice_date = "invoice_date"


INVOICE_ORDER_MAPPING = {
    "name": "name",
    "invoice_date": "invoice_date",
}


class InvoiceStateEnum(str, Enum):
    draft = "draft"
    posted = "posted"
    cancelled = "cancelled"


class InvoicePaymentStateEnum(str, Enum):
    not_paid = "not_paid"
    partial = "partial"
    paid = "paid"
    overdue = "overdue"


class InvoicePayment(PmsBaseModel):
    # The field is called date in account.payment, so we can map it accordingly.
    paymentDate: date
    amount: float = Field(0.0, alias="amount")
    currency_id: CurrencySummary = Field(alias="currency")
    ref: str

    @classmethod
    def from_account_payment(cls, account_payment):
        record = account_payment.read()[0]
        model_fields = cls.model_fields.keys()
        data = {k: v for k, v in record.items() if v and k in model_fields}
        data["paymentDate"] = account_payment.date
        if account_payment.currency_id:
            data["currency_id"] = CurrencySummary.from_res_currency(
                account_payment.currency_id
            )
        return cls(**data)


class InvoiceSummary(PmsBaseModel):
    id: int
    name: str = Field(alias="name")
    partner_id: ContactId | None = Field(None, alias="partnerId")
    invoice_date: date | None = Field(None, alias="invoiceDate")
    ref: str | None = Field(None, alias="reference")
    amount_total_signed: float = Field(0.0, alias="totalAmount")
    currency_id: CurrencySummary = Field(alias="currency")
    state: InvoiceStateEnum
    paymentState: InvoicePaymentStateEnum
    min_overdue_date: date | None = Field(None, alias="overdueDate")
    payments: list[InvoicePayment] = Field(default_factory=list)

    @classmethod
    def from_account_move(cls, account_move):
        record = account_move.read()[0]
        model_fields = cls.model_fields.keys()
        data = {k: v for k, v in record.items() if v and k in model_fields}
        if account_move.partner_id:
            data["partner_id"] = ContactId.from_res_partner(account_move.partner_id)
        if account_move.currency_id:
            data["currency_id"] = CurrencySummary.from_res_currency(
                account_move.currency_id
            )
        if account_move.invoice_payments_widget:
            payment_ids = [
                x["account_payment_id"]
                for x in account_move.invoice_payments_widget["content"]
                if x["account_payment_id"]
            ]
            payments = account_move.env["account.payment"].browse(payment_ids)
            data["payments"] = [
                InvoicePayment.from_account_payment(x) for x in payments
            ]
        if account_move.has_overdue_payments:
            data["paymentState"] = InvoicePaymentStateEnum.overdue
        elif account_move.payment_state == "in_payment":
            data["paymentState"] = InvoicePaymentStateEnum.not_paid
        else:
            data["paymentState"] = InvoicePaymentStateEnum(account_move.payment_state)
        return cls(**data)


class InvoiceSearch(BaseSearch):
    def __init__(
        self,
        pmsProperty: int | None = Query(
            default=None,
            description="Filter guests of the given property.",
        ),
        globalSearch: str | None = Query(
            default=None,
            description="Search across number, origin, reference, "
            "payment reference, contact(email, vat, name).",
        ),
        name: str | None = Query(
            default=None,
            description="Filter by invoice number.",
        ),
        reference: str | None = Query(
            default=None,
            description="Filter by invoice reference.",
        ),
        priceTotal: float | None = Query(
            default=None,
            description="Filter by total amount.",
        ),
        paymentState: Annotated[
            InvoicePaymentStateEnum | None,
            Query(
                description="Filter by payment state.",
            ),
        ] = None,
        state: Annotated[
            InvoiceStateEnum | None,
            Query(
                description="Filter by invoice state.",
            ),
        ] = None,
        invoiceDateFrom: Annotated[
            date | None,
            Query(
                description="Filter between invoice dates "
                "(only works if invoiceDateTo is also setted). "
            ),
        ] = None,
        invoiceDateTo: Annotated[
            date | None,
            Query(
                description="Filter between invoice dates "
                "(only works if invoiceDateFrom is also setted)."
            ),
        ] = None,
    ):
        self.pmsProperty = pmsProperty
        self.globalSearch = globalSearch
        self.name = name
        self.reference = reference
        self.priceTotal = priceTotal
        self.paymentState = paymentState
        self.state = state
        self.invoiceDateFrom = invoiceDateFrom
        self.invoiceDateTo = invoiceDateTo

    def to_odoo_domain(self, env: api.Environment) -> list:
        domain = []
        if self.pmsProperty:
            domain = expression.AND(
                [
                    domain,
                    [("pms_property_id", "=", self.pmsProperty)],
                ]
            )
        else:
            domain = expression.AND(
                [
                    domain,
                    [("pms_property_id", "in", env.user.pms_property_ids.ids)],
                ]
            )
        if self.globalSearch:
            domain = expression.AND(
                [
                    domain,
                    [
                        "|",
                        "|",
                        "|",
                        "|",
                        ("name", "ilike", self.globalSearch),
                        ("invoice_origin", "ilike", self.globalSearch),
                        ("ref", "ilike", self.globalSearch),
                        ("payment_reference", "ilike", self.globalSearch),
                        ("partner_id", "child_of", self.globalSearch),
                    ],
                ]
            )
        if self.name:
            domain = expression.AND([domain, [("name", "ilike", self.name)]])
        if self.reference:
            domain = expression.AND([domain, [("ref", "ilike", self.reference)]])
        if self.priceTotal:
            domain = expression.AND([domain, [("amount_total", "=", self.priceTotal)]])
        if self.paymentState:
            if self.paymentState == InvoicePaymentStateEnum.overdue:
                domain = expression.AND([domain, [("has_overdue_payments", "=", True)]])
            elif self.paymentState == InvoicePaymentStateEnum.not_paid:
                domain = expression.AND(
                    [
                        domain,
                        expression.AND(
                            [
                                [("has_overdue_payments", "=", False)],
                                [
                                    "|",
                                    ("payment_state", "=", "in_payment"),
                                    ("payment_state", "=", "not_paid"),
                                ],
                            ]
                        ),
                    ]
                )
            else:
                domain = expression.AND(
                    [domain, [("payment_state", "=", self.paymentState.value)]]
                )
        if self.state:
            domain = expression.AND([domain, [("state", "=", self.state)]])
        if self.invoiceDateFrom and self.invoiceDateTo:
            domain = expression.AND(
                [
                    domain,
                    [
                        ("invoice_date", ">=", self.invoiceDateFrom),
                        ("invoice_date", "<=", self.invoiceDateTo),
                    ],
                ]
            )
        return domain
