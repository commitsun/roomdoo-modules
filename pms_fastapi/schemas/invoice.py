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
from .payment_term import PaymentTermId
from .pms_folio import FolioId


class ShareUrl(PmsBaseModel):
    url: str


class FolioInvoiceLine(PmsBaseModel):
    saleLineId: int = Field(
        alias="saleLineId",
        description="ID of the folio sale line to invoice.",
    )
    description: str = Field(
        description=(
            "Final description for the invoice line. May differ from the "
            "original sale line description."
        ),
    )
    quantityToInvoice: float = Field(
        alias="quantityToInvoice",
        gt=0,
        description=(
            "Quantity to invoice for this line. Must be greater than 0 and "
            "less than or equal to the pending quantity of the sale line."
        ),
    )


class FolioInvoiceCreate(PmsBaseModel):
    validate_invoice: bool = Field(
        alias="validate",
        description=(
            "If true, the invoice is posted immediately after creation. "
            "If false, it is created as a draft."
        ),
    )
    customerId: int | None = Field(
        None,
        alias="customerId",
        description=(
            "Customer for the invoice. Null creates a simplified invoice. "
            "Subject to simplifiedInvoiceLimit validation server-side."
        ),
    )
    invoiceDate: date | None = Field(
        None,
        alias="invoiceDate",
        description="Invoice date. Server defaults to today if null/omitted.",
    )
    dueDate: date | None = Field(
        None,
        alias="dueDate",
        description=(
            "Invoice due date. Server defaults from payment terms if null/omitted."
        ),
    )
    narration: str = Field("", description="Internal notes. Optional on create.")
    lines: list[FolioInvoiceLine] = Field(
        min_length=1,
        description=(
            "Sale lines to invoice. Must contain at least one line. All "
            "lines must belong to the same property. Lines can reference "
            "sale lines from different folios."
        ),
    )
    downpaymentLines: list[int] = Field(
        default_factory=list,
        description=(
            "Ids of down-payment invoices to subtract from the invoice. "
            "Must belong to the same folios as the invoiced lines. Send [] "
            "to subtract no down payment."
        ),
    )


class InvoiceOrderField(str, Enum):
    name = "name"
    invoice_date = "invoice_date"


INVOICE_ORDER_MAPPING = {
    "name": "name",
    "invoice_date": "invoice_date",
}


class ReportFormatEnum(str, Enum):
    pdf = "pdf"
    xlsx = "xlsx"


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
    "in_payment": InvoicePaymentStateEnum.paid,
    "paid": InvoicePaymentStateEnum.paid,
    "partial": InvoicePaymentStateEnum.partial,
    "reversed": InvoicePaymentStateEnum.reversed,
    "invoicing_legacy": InvoicePaymentStateEnum.paid,
}


class InvoicePaymentTypeEnum(str, Enum):
    payment = "payment"
    invoice = "invoice"
    entry = "entry"
    refund = "refund"


MOVE_TYPE_TO_PAYMENT_TYPE = {
    "out_invoice": InvoicePaymentTypeEnum.invoice,
    "in_invoice": InvoicePaymentTypeEnum.invoice,
    "out_refund": InvoicePaymentTypeEnum.refund,
    "in_refund": InvoicePaymentTypeEnum.refund,
    "entry": InvoicePaymentTypeEnum.entry,
}


class InvoicePayment(PmsBaseModel):
    id: str = Field(
        description=(
            "Composite identifier of the reconciliation entry in the form "
            "'{type}_{id}'. Used as {reconciliation_id} when calling DELETE "
            "/invoices/{invoice_id}/reconciliations/{reconciliation_id} "
            "(only entries with paymentType='payment' can be removed manually)."
        ),
    )
    paymentType: InvoicePaymentTypeEnum = Field(alias="paymentType")
    paymentDate: date
    paymentMethod: PaymentMethodSummary | None = None
    journal: JournalSummary | None = None
    paymentAmount: float = Field(0.0, alias="paymentAmount")
    amount: float = Field(0.0, alias="amount")
    currency_id: CurrencySummary = Field(alias="currency")
    paymentAmountCompany: float = Field(0.0, alias="paymentAmountCompany")
    companyCurrency: CurrencySummary = Field(alias="companyCurrency")
    ref: str = ""

    @classmethod
    def from_widget_item(cls, widget_item, env):
        """Build from an invoice_payments_widget content item.

        Handles all reconciliation types: payment, refund, invoice, entry.
        """
        counterpart_move = env["account.move"].browse(widget_item["move_id"])
        payment_id = widget_item.get("account_payment_id")

        if payment_id:
            payment_type = InvoicePaymentTypeEnum.payment
        else:
            payment_type = MOVE_TYPE_TO_PAYMENT_TYPE.get(
                counterpart_move.move_type, InvoicePaymentTypeEnum.entry
            )

        partial = env["account.partial.reconcile"].browse(widget_item["partial_id"])
        widget_currency = env["res.currency"].browse(widget_item["currency_id"])
        company_currency = partial.company_currency_id
        company = partial.company_id

        if payment_id:
            payment = env["account.payment"].browse(payment_id)
            composite_id = f"payment_{payment.id}"
            total_currency = payment.currency_id or company_currency
            total_amount = payment.amount
            total_date = payment.date
        else:
            composite_id = f"{payment_type.value}_{counterpart_move.id}"
            total_currency = counterpart_move.currency_id or company_currency
            total_amount = counterpart_move.amount_total
            total_date = counterpart_move.invoice_date or counterpart_move.date

        if total_currency == widget_currency:
            payment_amount_widget = total_amount
        else:
            payment_amount_widget = total_currency._convert(
                total_amount, widget_currency, company, total_date
            )
        if total_currency == company_currency:
            payment_amount_company = total_amount
        else:
            payment_amount_company = total_currency._convert(
                total_amount, company_currency, company, total_date
            )

        data = {
            "id": composite_id,
            "paymentType": payment_type,
            "paymentDate": widget_item["date"],
            "paymentAmount": widget_currency.round(payment_amount_widget),
            "amount": widget_currency.round(widget_item["amount"]),
            "ref": counterpart_move.name or "",
            "currency_id": CurrencySummary.from_res_currency(widget_currency),
            "paymentAmountCompany": company_currency.round(payment_amount_company),
            "companyCurrency": CurrencySummary.from_res_currency(company_currency),
        }

        if counterpart_move.journal_id:
            data["journal"] = JournalSummary.from_account_journal(
                counterpart_move.journal_id
            )

        if payment_id:
            payment = env["account.payment"].browse(payment_id)
            if payment.payment_method_line_id:
                data[
                    "paymentMethod"
                ] = PaymentMethodSummary.from_account_payment_method_line(
                    payment.payment_method_line_id
                )

        return cls(**data)


class ReconciliationCreate(PmsBaseModel):
    paymentId: str = Field(
        alias="paymentId",
        pattern=r"^payment_\d+$",
        description=(
            "Composite identifier of the reconcilable payment, in the form "
            "'{type}_{id}'. Today only 'payment_{id}' is accepted. Obtained "
            "from GET /invoices/{invoice_id}/reconcilable-payments."
        ),
    )


class ReconcilablePayment(PmsBaseModel):
    id: str = Field(
        description=(
            "Composite identifier '{type}_{id}'. Today only 'payment_{id}' "
            "is returned. The type prefix is reserved so the contract can "
            "grow to other reconcilable document types without breaking."
        ),
    )
    paymentType: InvoicePaymentTypeEnum = Field(alias="paymentType")
    paymentDate: date = Field(alias="paymentDate")
    paymentMethod: PaymentMethodSummary | None = None
    journal: JournalSummary | None = None
    paymentAmount: float = Field(alias="paymentAmount")
    availableAmount: float = Field(alias="availableAmount")
    currency: CurrencySummary
    paymentAmountCompany: float = Field(alias="paymentAmountCompany")
    availableAmountCompany: float = Field(alias="availableAmountCompany")
    companyCurrency: CurrencySummary = Field(alias="companyCurrency")
    ref: str = ""

    @classmethod
    def from_account_payment(
        cls,
        payment,
        available_currency: float,
        available_company: float,
    ):
        company_currency = payment.company_id.currency_id
        pay_currency = payment.currency_id or company_currency
        payment_amount = payment.amount
        if pay_currency == company_currency:
            payment_amount_company = payment_amount
        else:
            payment_amount_company = pay_currency._convert(
                payment_amount,
                company_currency,
                payment.company_id,
                payment.date,
            )
        data = {
            "id": f"payment_{payment.id}",
            "paymentType": InvoicePaymentTypeEnum.payment,
            "paymentDate": payment.date,
            "paymentAmount": pay_currency.round(payment_amount),
            "availableAmount": pay_currency.round(available_currency),
            "currency": CurrencySummary.from_res_currency(pay_currency),
            "paymentAmountCompany": company_currency.round(payment_amount_company),
            "availableAmountCompany": company_currency.round(available_company),
            "companyCurrency": CurrencySummary.from_res_currency(company_currency),
            "ref": payment.ref or "",
        }
        if payment.journal_id:
            data["journal"] = JournalSummary.from_account_journal(payment.journal_id)
        if payment.payment_method_line_id:
            data[
                "paymentMethod"
            ] = PaymentMethodSummary.from_account_payment_method_line(
                payment.payment_method_line_id
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
    amount_residual_signed: CurrencyAmount = Field(0.0, alias="pendingAmount")
    currency_id: CurrencySummary = Field(alias="currency")
    state: InvoiceStateEnum
    paymentState: InvoicePaymentStateEnum
    min_overdue_date: date | None = Field(None, alias="overdueDate")
    payments: list[InvoicePayment] = Field(default_factory=list)
    folio_ids: list[FolioId] = Field(default_factory=list, alias="folios")

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
            data["payments"] = [
                InvoicePayment.from_widget_item(item, account_move.env)
                for item in account_move.invoice_payments_widget["content"]
            ]
        data["folio_ids"] = [
            FolioId.from_pms_folio(folio) for folio in account_move.folio_ids
        ]
        if account_move.has_overdue_payments:
            data["paymentState"] = InvoicePaymentStateEnum.overdue
        else:
            data["paymentState"] = ODOO_PAYMENT_STATE_MAP.get(
                account_move.payment_state, InvoicePaymentStateEnum.not_paid
            )
        return cls(**data)


class FolioLineInput(PmsBaseModel):
    id: int = Field(description="Id of the folio line being invoiced.")
    quantity: float = Field(
        gt=0,
        description="Quantity of this line to invoice.",
    )
    description: str = Field(
        description="Final description for the line on the invoice. May be empty."
    )


class InvoiceInput(PmsBaseModel):
    partner: int = Field(description="Customer id the invoice is issued to.")
    invoiceDate: date | None = Field(
        description="Invoice date. Null lets the server set the current date.",
    )
    invoiceDateDue: date | None = Field(
        description="Due date. Null lets the server compute it from payment terms.",
    )
    narration: str = Field(description="Internal notes. May be empty.")
    folioLines: list[FolioLineInput] = Field(
        description=(
            "Folio lines to invoice. Full replacement of the previous composition."
        ),
    )
    downpaymentLines: list[int] = Field(
        description=(
            "Ids of down-payment invoices to subtract. Full replacement of the "
            "previous composition."
        ),
    )


class FolioLineDetail(PmsBaseModel):
    id: int
    quantity: float
    description: str = ""


class InvoiceLine(PmsBaseModel):
    id: int
    description: str = ""
    quantity: float
    priceUnit: float = Field(0.0, alias="priceUnit")
    discount: float = 0.0
    priceSubtotal: float = Field(0.0, alias="priceSubtotal")
    priceTotal: float = Field(0.0, alias="priceTotal")
    folioLineIds: list[int] = Field(
        default_factory=list,
        alias="folioLineIds",
        description="Ids of the folio sale lines this invoice line was billed from.",
    )

    @classmethod
    def from_account_move_line(cls, line):
        return cls(
            id=line.id,
            description=line.name or "",
            quantity=line.quantity,
            priceUnit=line.price_unit,
            discount=line.discount,
            priceSubtotal=line.price_subtotal,
            priceTotal=line.price_total,
            folioLineIds=line.folio_line_ids.ids,
        )


class InvoiceRef(PmsBaseModel):
    id: int
    name: str

    @classmethod
    def from_account_move(cls, move):
        return cls(id=move.id, name=move.name or "")


class InvoiceDetail(PmsBaseModel):
    id: int
    name: str = ""
    move_type: InvoiceTypeEnum = Field(alias="invoiceType")
    state: InvoiceStateEnum
    paymentState: InvoicePaymentStateEnum
    partner_id: ContactId | None = Field(None, alias="partner")
    invoice_date: date | None = Field(None, alias="invoiceDate")
    invoice_date_due: date | None = Field(None, alias="invoiceDateDue")
    paymentTerm: PaymentTermId | None = None
    journal: JournalSummary | None = None
    ref: str = Field("", alias="reference")
    narration: str = ""
    amount_total_signed: CurrencyAmount = Field(0.0, alias="totalAmount")
    currency_id: CurrencySummary = Field(alias="currency")
    folioLines: list[FolioLineDetail] = Field(default_factory=list)
    downpaymentLines: list[int] = Field(default_factory=list)
    lines: list[InvoiceLine] = Field(default_factory=list)
    payments: list[InvoicePayment] = Field(default_factory=list)
    refundedInvoice: InvoiceRef | None = None
    refundedBy: InvoiceRef | None = None
    replaces: InvoiceRef | None = None
    replacedBy: InvoiceRef | None = None

    @classmethod
    def from_account_move(cls, account_move):
        data = cls._read_odoo_record(account_move)
        data["move_type"] = ODOO_INVOICE_TYPE_REVERSE_MAP.get(account_move.move_type)
        data["narration"] = account_move.narration or ""
        data["ref"] = account_move.ref or ""
        if account_move.partner_id:
            data["partner_id"] = ContactId.from_res_partner(account_move.partner_id)
        if account_move.currency_id:
            data["_decimal_places"] = account_move.currency_id.decimal_places
            data["currency_id"] = CurrencySummary.from_res_currency(
                account_move.currency_id
            )
        if account_move.invoice_payment_term_id:
            data["paymentTerm"] = PaymentTermId.from_account_payment_term(
                account_move.invoice_payment_term_id
            )
        if account_move.journal_id:
            data["journal"] = JournalSummary.from_account_journal(
                account_move.journal_id
            )
        if account_move.has_overdue_payments:
            data["paymentState"] = InvoicePaymentStateEnum.overdue
        else:
            data["paymentState"] = ODOO_PAYMENT_STATE_MAP.get(
                account_move.payment_state, InvoicePaymentStateEnum.not_paid
            )
        invoice_lines = account_move.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        folio_lines = invoice_lines.mapped("folio_line_ids").filtered(
            lambda fl: not fl.is_downpayment
        )
        downpayment_invoices = (
            invoice_lines.mapped("folio_line_ids")
            .filtered("is_downpayment")
            .invoice_lines.move_id
        )
        data["folioLines"] = [
            FolioLineDetail(
                id=fl.id,
                quantity=cls._invoiced_quantity(fl, invoice_lines),
                description=cls._invoiced_description(fl, invoice_lines),
            )
            for fl in folio_lines
        ]
        data["downpaymentLines"] = downpayment_invoices.ids
        data["lines"] = [
            InvoiceLine.from_account_move_line(line) for line in invoice_lines
        ]
        if account_move.invoice_payments_widget:
            data["payments"] = [
                InvoicePayment.from_widget_item(item, account_move.env)
                for item in account_move.invoice_payments_widget["content"]
            ]
        if account_move.reversed_entry_id:
            data["refundedInvoice"] = InvoiceRef.from_account_move(
                account_move.reversed_entry_id
            )
        refund = account_move.reversal_move_id[:1]
        if refund:
            data["refundedBy"] = InvoiceRef.from_account_move(refund)
        if account_move.replaces_invoice_id:
            data["replaces"] = InvoiceRef.from_account_move(
                account_move.replaces_invoice_id
            )
        replacement = account_move.replaced_by_invoice_ids[:1]
        if replacement:
            data["replacedBy"] = InvoiceRef.from_account_move(replacement)
        return cls(**data)

    @staticmethod
    def _invoiced_quantity(folio_line, invoice_lines):
        return sum(
            line.quantity for line in invoice_lines if folio_line in line.folio_line_ids
        )

    @staticmethod
    def _invoiced_description(folio_line, invoice_lines):
        descriptions = [
            line.name
            for line in invoice_lines
            if folio_line in line.folio_line_ids and line.name
        ]
        return descriptions[0] if descriptions else ""


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
        totalAmountGt: Annotated[
            float | None,
            Query(
                description="Filter invoices whose total amount is greater than "
                "this value.",
            ),
        ] = None,
        totalAmountLt: Annotated[
            float | None,
            Query(
                description="Filter invoices whose total amount is less than "
                "this value.",
            ),
        ] = None,
        totalAmountEq: Annotated[
            float | None,
            Query(
                description="Filter invoices whose total amount is equal to "
                "this value.",
            ),
        ] = None,
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
        self.totalAmountGt = totalAmountGt
        self.totalAmountLt = totalAmountLt
        self.totalAmountEq = totalAmountEq
        self.paymentState = paymentState
        self.state = state
        self.invoiceDateFrom = invoiceDateFrom
        self.invoiceDateTo = invoiceDateTo
        self.journal = journal
        self.paymentMethod = paymentMethod
        self.partner = partner

    @staticmethod
    def _payment_state_domain(payment_states: list) -> list:
        overdue_selected = InvoicePaymentStateEnum.overdue in payment_states
        not_paid_selected = InvoicePaymentStateEnum.not_paid in payment_states
        if overdue_selected and not_paid_selected:
            # Optimization: avoid OR(subquery, payment_state_condition).
            # _search_has_overdue_payments returns a subquery on
            # account_move_line; when ORed with a payment_state condition,
            # PostgreSQL cannot use the payment_state index and falls back
            # to a seq scan.
            #
            # Overdue invoices only have payment_state 'not_paid' or 'partial'.
            # notPaid already covers 'not_paid' and 'in_payment', so we only
            # need the subquery for 'partial' invoices. Using AND(partial,
            # subquery) lets PostgreSQL filter by payment_state first (index)
            # and run the subquery on a much smaller set.
            state_domains = [
                [
                    "|",
                    ("payment_state", "=", "in_payment"),
                    ("payment_state", "=", "not_paid"),
                ],
                expression.AND(
                    [
                        [("payment_state", "=", "partial")],
                        [("has_overdue_payments", "=", True)],
                    ]
                ),
            ]
            for ps in payment_states:
                if ps not in (
                    InvoicePaymentStateEnum.overdue,
                    InvoicePaymentStateEnum.not_paid,
                ):
                    if ps == InvoicePaymentStateEnum.paid:
                        state_domains.append(
                            [
                                "|",
                                ("payment_state", "=", "paid"),
                                ("payment_state", "=", "invoicing_legacy"),
                            ]
                        )
                    else:
                        state_domains.append([("payment_state", "=", ps.value)])
        else:
            state_domains = []
            for ps in payment_states:
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
                elif ps == InvoicePaymentStateEnum.paid:
                    state_domains.append(
                        [
                            "|",
                            ("payment_state", "=", "paid"),
                            ("payment_state", "=", "invoicing_legacy"),
                        ]
                    )
                else:
                    state_domains.append([("payment_state", "=", ps.value)])
        return expression.OR(state_domains)

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
        if self.totalAmountGt is not None:
            domain = expression.AND(
                [domain, [("amount_total_signed", ">", self.totalAmountGt)]]
            )
        if self.totalAmountLt is not None:
            domain = expression.AND(
                [domain, [("amount_total_signed", "<", self.totalAmountLt)]]
            )
        if self.totalAmountEq is not None:
            domain = expression.AND(
                [domain, [("amount_total_signed", "=", self.totalAmountEq)]]
            )
        if self.paymentState:
            domain = expression.AND(
                [domain, self._payment_state_domain(self.paymentState)]
            )
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
                [domain, [("payment_method_ids", "in", self.paymentMethod)]]
            )
        if self.partner:
            domain = expression.AND(
                [domain, [("partner_id", "child_of", self.partner)]]
            )
        return domain
