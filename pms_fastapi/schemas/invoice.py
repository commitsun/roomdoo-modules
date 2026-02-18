from datetime import date
from enum import Enum
from typing import Annotated

from fastapi import Query
from pydantic import Field

from odoo import api
from odoo.osv import expression

from .base import BaseSearch, CurrencyAmount, PmsBaseModel
from .contact import ContactId
from .currency import CurrencySummary
from .journal import JournalSummary
from .payment_method import PaymentMethodSummary


class InvoiceOrderField(str, Enum):
    name = "name"
    invoice_date = "invoice_date"


INVOICE_ORDER_MAPPING = {
    "name": "name",
    "invoice_date": "invoice_date",
}


class InvoiceTypeEnum(str, Enum):
    outInvoice = "outInvoice"
    outRefund = "outRefund"


ODOO_INVOICE_TYPE_MAP = {
    InvoiceTypeEnum.outInvoice: "out_invoice",
    InvoiceTypeEnum.outRefund: "out_refund",
}

ODOO_INVOICE_TYPE_REVERSE_MAP = {v: k for k, v in ODOO_INVOICE_TYPE_MAP.items()}


class InvoiceStateEnum(str, Enum):
    draft = "draft"
    posted = "posted"
    cancelled = "cancel"


class InvoicePaymentStateEnum(str, Enum):
    not_paid = "notPaid"
    partial = "partial"
    paid = "paid"
    overdue = "overdue"
    reversed = "reversed"


ODOO_PAYMENT_STATE_MAP = {
    "not_paid": InvoicePaymentStateEnum.not_paid,
    "in_payment": InvoicePaymentStateEnum.not_paid,
    "paid": InvoicePaymentStateEnum.paid,
    "partial": InvoicePaymentStateEnum.partial,
    "reversed": InvoicePaymentStateEnum.reversed,
    "invoicing_legacy": InvoicePaymentStateEnum.not_paid,
}


class InvoicePayment(PmsBaseModel):
    # The field is called date in account.payment, so we can map it accordingly.
    paymentDate: date
    paymentMethod: PaymentMethodSummary | None = None
    journal: JournalSummary | None = None
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
        if account_payment.payment_method_line_id:
            data[
                "paymentMethod"
            ] = PaymentMethodSummary.from_account_payment_method_line(
                account_payment.payment_method_line_id
            )
        if account_payment.journal_id:
            data["journal"] = JournalSummary.from_account_journal(
                account_payment.journal_id
            )
        return cls(**data)


class InvoiceSummary(PmsBaseModel):
    id: int
    name: str = Field(alias="name")
    move_type: InvoiceTypeEnum | None = Field(None, alias="invoiceType")
    partner_id: ContactId | None = Field(None, alias="partner")
    invoice_date: date | None = Field(None, alias="invoiceDate")
    ref: str | None = Field(None, alias="reference")
    amount_total_signed: CurrencyAmount = Field(0.0, alias="totalAmount")
    currency_id: CurrencySummary = Field(alias="currency")
    state: InvoiceStateEnum
    paymentState: InvoicePaymentStateEnum
    min_overdue_date: date | None = Field(None, alias="overdueDate")
    payments: list[InvoicePayment] = Field(default_factory=list)

    @classmethod
    def from_account_move(cls, account_move):
        data = cls._read_odoo_record(account_move)
        data["move_type"] = ODOO_INVOICE_TYPE_REVERSE_MAP.get(account_move.move_type)
        if account_move.partner_id:
            data["partner_id"] = ContactId.from_res_partner(account_move.partner_id)
        if account_move.currency_id:
            data["_decimal_places"] = account_move.currency_id.decimal_places
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
        else:
            data["paymentState"] = ODOO_PAYMENT_STATE_MAP.get(
                account_move.payment_state, InvoicePaymentStateEnum.not_paid
            )
        return cls(**data)


class InvoiceSearch(BaseSearch):
    def __init__(
        self,
        pmsPropertyId: int | None = Query(
            default=None,
            description="Filter guests of the given property.",
        ),
        globalSearch: str | None = Query(
            default=None,
            description="Search across number, origin, reference, "
            "payment reference, contact(email, vat, name).",
        ),
        invoiceType: Annotated[
            InvoiceTypeEnum | None,
            Query(
                description="Filter by invoice type.",
            ),
        ] = None,
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
            list[InvoicePaymentStateEnum] | None,
            Query(
                description="Filter by payment state. Use repeated query parameters, "
                "e.g., ?paymentState=paid&paymentState=notPaid",
            ),
        ] = None,
        state: Annotated[
            list[InvoiceStateEnum] | None,
            Query(
                description="Filter by invoice state. Use repeated query parameters, "
                "e.g., ?state=draft&state=posted",
            ),
        ] = None,
        invoiceDateFrom: Annotated[
            date | None,
            Query(
                description="Filter between invoice dates "
                "(only works if invoiceDateTo is also set). "
            ),
        ] = None,
        invoiceDateTo: Annotated[
            date | None,
            Query(
                description="Filter between invoice dates "
                "(only works if invoiceDateFrom is also set)."
            ),
        ] = None,
        journal: Annotated[
            list[int] | None,
            Query(
                description="Filter by journal id. Use repeated query parameters, "
                "e.g., ?journal=1&journal=2",
            ),
        ] = None,
        paymentMethod: Annotated[
            list[int] | None,
            Query(
                description="Filter by payment method id. Use repeated query "
                "parameters, e.g., ?paymentMethod=1&paymentMethod=2",
            ),
        ] = None,
        partner: str | None = Query(
            default=None,
            description="Filter by partner name.",
        ),
    ):
        self.pmsProperty = pmsPropertyId
        self.globalSearch = globalSearch
        self.name = name
        self.invoiceType = invoiceType
        self.reference = reference
        self.priceTotal = priceTotal
        self.paymentState = paymentState
        self.state = state
        self.invoiceDateFrom = invoiceDateFrom
        self.invoiceDateTo = invoiceDateTo
        self.journal = journal
        self.paymentMethod = paymentMethod
        self.partner = partner

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
        if self.invoiceType:
            domain = expression.AND(
                [
                    domain,
                    [("move_type", "=", ODOO_INVOICE_TYPE_MAP[self.invoiceType])],
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
            state_domains = []
            for ps in self.paymentState:
                if ps == InvoicePaymentStateEnum.overdue:
                    state_domains.append([("has_overdue_payments", "=", True)])
                elif ps == InvoicePaymentStateEnum.not_paid:
                    state_domains.append(
                        expression.AND(
                            [
                                [("has_overdue_payments", "=", False)],
                                [
                                    "|",
                                    ("payment_state", "=", "in_payment"),
                                    ("payment_state", "=", "not_paid"),
                                ],
                            ]
                        )
                    )
                else:
                    state_domains.append([("payment_state", "=", ps.value)])
            domain = expression.AND([domain, expression.OR(state_domains)])
        if self.state:
            domain = expression.AND(
                [domain, [("state", "in", [s.value for s in self.state])]]
            )
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
        if self.journal:
            domain = expression.AND([domain, [("journal_id", "in", self.journal)]])
        if self.paymentMethod:
            domain = expression.AND(
                [domain, [("payment_method_line_id", "in", self.paymentMethod)]]
            )
        if self.partner:
            domain = expression.AND(
                [domain, [("partner_id", "child_of", self.partner)]]
            )
        return domain
