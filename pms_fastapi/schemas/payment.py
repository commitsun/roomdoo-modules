import datetime
from datetime import date
from enum import Enum
from typing import Annotated

from fastapi import HTTPException, Query
from pydantic import Field

from odoo import api
from odoo.osv import expression

from .base import BaseSearch, CurrencyAmount, PmsBaseModel
from .contact import ContactId
from .currency import CurrencySummary
from .payment_method import PaymentMethodSummary
from .pms_folio import FolioId
from .user import UserId


class PaymentTypeEnum(str, Enum):
    customerPayment = "customerPayment"
    customerRefund = "customerRefund"
    supplierPayment = "supplierPayment"
    supplierRefund = "supplierRefund"
    internalTransfer = "internalTransfer"


# Maps the API payment type to the internal `pms_api_transaction_type`
# computed on account.payment (see pms_api_rest/models/account_payment.py).
ENUM_TO_TRANSACTION_TYPE = {
    PaymentTypeEnum.customerPayment: "customer_inbound",
    PaymentTypeEnum.customerRefund: "customer_outbound",
    PaymentTypeEnum.supplierPayment: "supplier_outbound",
    PaymentTypeEnum.supplierRefund: "supplier_inbound",
    PaymentTypeEnum.internalTransfer: "internal_transfer",
}
TRANSACTION_TYPE_TO_ENUM = {v: k for k, v in ENUM_TO_TRANSACTION_TYPE.items()}

# (payment_type, partner_type) for the non-transfer transaction types.
_TRANSACTION_TYPE_TO_FIELDS = {
    "customer_inbound": ("inbound", "customer"),
    "customer_outbound": ("outbound", "customer"),
    "supplier_outbound": ("outbound", "supplier"),
    "supplier_inbound": ("inbound", "supplier"),
}


class PaymentCreateType(str, Enum):
    """Payment types accepted on manual creation via POST /payments.

    Refunds and internal transfers are NOT created here (transfers have their
    own endpoint); the response enum (PaymentTypeEnum) still returns all five.
    """

    customerPayment = "customerPayment"
    supplierPayment = "supplierPayment"


class PaymentInput(PmsBaseModel):
    paymentType: PaymentCreateType
    amount: CurrencyAmount = Field(gt=0, description="Always positive; > 0.")
    date: date
    paymentMethodId: int = Field(
        description="account.payment.method.line id (the front's 'payment mode'). "
        "The journal is derived from it."
    )
    partnerId: int | None = Field(
        None,
        description="Required for supplierPayment; for customerPayment may be "
        "null and derived from the folio/invoice context.",
    )
    folioId: int | None = Field(
        None, description="Folio context. Mutually exclusive with invoiceId."
    )
    invoiceId: int | None = Field(
        None, description="Invoice context. Mutually exclusive with folioId."
    )
    reference: str = ""


class PaymentUpdate(PmsBaseModel):
    """Partial edit of a registered payment (amount, date, payment method).

    Only the modified fields are sent; omitted fields are left untouched.
    """

    amount: CurrencyAmount | None = Field(
        None, gt=0, description="New amount. Always positive; > 0 (422 otherwise)."
    )
    # `datetime.date` (not the bare `date` name) to avoid the field name
    # shadowing the type when the default value is assigned.
    date: datetime.date | None = Field(None, description="New payment date.")
    paymentMethodId: int | None = Field(
        None,
        description="New payment method (the front's 'payment mode'). The journal "
        "is derived from it.",
    )


class InternalTransferInput(PmsBaseModel):
    amount: CurrencyAmount = Field(gt=0, description="Always positive; > 0.")
    date: date
    originPaymentMethodId: int = Field(
        description="account.payment.method.line id (outbound) the money leaves "
        "from. The origin journal is derived from it."
    )
    destinationPaymentMethodId: int = Field(
        description="account.payment.method.line id (inbound) the money goes to. "
        "The destination journal is derived from it."
    )
    reason: str = ""


class ReportFormatEnum(str, Enum):
    pdf = "pdf"
    xlsx = "xlsx"


class PaymentOrderField(str, Enum):
    date = "date"


PAYMENT_ORDER_MAPPING = {
    "date": "date",
}


class ReconciledInvoiceTypeEnum(str, Enum):
    outInvoice = "outInvoice"
    outRefund = "outRefund"
    inInvoice = "inInvoice"
    inRefund = "inRefund"


ODOO_MOVE_TYPE_TO_RECONCILED_ENUM = {
    "out_invoice": ReconciledInvoiceTypeEnum.outInvoice,
    "out_refund": ReconciledInvoiceTypeEnum.outRefund,
    "in_invoice": ReconciledInvoiceTypeEnum.inInvoice,
    "in_refund": ReconciledInvoiceTypeEnum.inRefund,
}


class ReconciledInvoice(PmsBaseModel):
    id: int
    name: str = ""
    move_type: ReconciledInvoiceTypeEnum = Field(alias="invoiceType")

    @classmethod
    def from_account_move(cls, move):
        return cls(
            id=move.id,
            name=move.name or "",
            move_type=ODOO_MOVE_TYPE_TO_RECONCILED_ENUM[move.move_type],
        )


class PaymentSummary(PmsBaseModel):
    id: int
    name: str = ""
    date: date
    partner_id: ContactId | None = Field(None, alias="partner")
    ref: str = Field("", alias="reference")
    folio: FolioId | None = None
    createdBy: UserId | None = None
    paymentType: PaymentTypeEnum
    paymentMethod: PaymentMethodSummary | None = None
    amount: CurrencyAmount = 0.0
    currency: CurrencySummary
    invoices: list[ReconciledInvoice] = Field(
        default_factory=list,
        description="Invoices and refunds this payment is reconciled against.",
    )

    @classmethod
    def from_account_payment(cls, payment):
        data = {
            "id": payment.id,
            "name": payment.name or "",
            "date": payment.date,
            "ref": payment.ref or "",
            "paymentType": TRANSACTION_TYPE_TO_ENUM[payment.pms_api_transaction_type],
            "amount": abs(payment.amount),
        }
        currency = payment.currency_id or payment.company_id.currency_id
        data["_decimal_places"] = currency.decimal_places
        data["currency"] = CurrencySummary.from_res_currency(currency)
        # Internal transfers carry the company partner internally (accounting),
        # but the contract reports no contact for them ('Sin asignar').
        if payment.partner_id and not payment.is_internal_transfer:
            data["partner_id"] = ContactId.from_res_partner(payment.partner_id)
        if payment.folio_ids:
            data["folio"] = FolioId.from_pms_folio(payment.folio_ids[:1])
        if payment.create_uid:
            data["createdBy"] = UserId.from_res_users(payment.create_uid)
        if payment.payment_method_line_id:
            data[
                "paymentMethod"
            ] = PaymentMethodSummary.from_account_payment_method_line(
                payment.payment_method_line_id
            )
        reconciled_invoices = (
            payment.reconciled_invoice_ids | payment.reconciled_bill_ids
        )
        if reconciled_invoices:
            data["invoices"] = [
                ReconciledInvoice.from_account_move(move)
                for move in reconciled_invoices
            ]
        return cls(**data)


class PaymentSearch(BaseSearch):
    def __init__(
        self,
        pmsPropertyId: int | None = Query(
            default=None,
            description="Filter payments of the given property. Defaults to the "
            "active property of the session.",
        ),
        globalSearch: str | None = Query(
            default=None,
            description="Free-text search across reference, contact and created by.",
        ),
        dateFrom: Annotated[
            date | None,
            Query(
                description="Start of the payment date range "
                "(only applies if dateTo is also set).",
            ),
        ] = None,
        dateTo: Annotated[
            date | None,
            Query(
                description="End of the payment date range "
                "(only applies if dateFrom is also set).",
            ),
        ] = None,
        paymentType: Annotated[
            list[PaymentTypeEnum] | None,
            Query(
                description="Filter by payment type. Use repeated query parameters, "
                "e.g., ?paymentType=customerPayment&paymentType=supplierPayment",
            ),
        ] = None,
        paymentMethod: Annotated[
            list[int] | None,
            Query(
                description="Filter by payment method id. Use repeated query "
                "parameters, e.g., ?paymentMethod=3&paymentMethod=9",
            ),
        ] = None,
        reference: str | None = Query(
            default=None,
            description="Filter exclusively by reference.",
        ),
        contact: str | None = Query(
            default=None,
            description="Filter by the name of the contact (partner) of the payment.",
        ),
        createdBy: str | None = Query(
            default=None,
            description="Filter by the name of the user who registered the payment.",
        ),
        amountEq: Annotated[
            float | None,
            Query(ge=0, description="Filter payments whose amount equals this value."),
        ] = None,
        amountGt: Annotated[
            float | None,
            Query(
                ge=0,
                description="Filter payments whose amount is greater than this value.",
            ),
        ] = None,
        amountLt: Annotated[
            float | None,
            Query(
                ge=0,
                description="Filter payments whose amount is less than this value.",
            ),
        ] = None,
    ):
        if dateFrom and dateTo and dateTo < dateFrom:
            raise HTTPException(
                status_code=422,
                detail="dateTo cannot be earlier than dateFrom.",
            )
        self.pmsPropertyId = pmsPropertyId
        self.globalSearch = globalSearch
        self.dateFrom = dateFrom
        self.dateTo = dateTo
        self.paymentType = paymentType
        self.paymentMethod = paymentMethod
        self.reference = reference
        self.contact = contact
        self.createdBy = createdBy
        self.amountEq = amountEq
        self.amountGt = amountGt
        self.amountLt = amountLt

    def _property_journal_ids(self, env: api.Environment) -> list:
        if self.pmsPropertyId:
            properties = env["pms.property"].sudo().browse(self.pmsPropertyId)
            PmsBaseModel.pms_api_check_access(env.user, properties)
        else:
            properties = env.user.pms_property_id or env.user.pms_property_ids
        journals = env["account.journal"]
        for prop in properties:
            journals |= prop._get_payment_methods(automatic_included=True).journal_id
        # Avoid exposing generic company journals not tied to any property.
        journals = journals.sudo().filtered(lambda j: j.pms_property_ids)
        return journals.ids

    def _payment_type_domain(self) -> list:
        type_domains = []
        for pt in self.paymentType:
            transaction_type = ENUM_TO_TRANSACTION_TYPE[pt]
            if transaction_type == "internal_transfer":
                type_domains.append([("is_internal_transfer", "=", True)])
            else:
                payment_type, partner_type = _TRANSACTION_TYPE_TO_FIELDS[
                    transaction_type
                ]
                type_domains.append(
                    [
                        ("is_internal_transfer", "=", False),
                        ("partner_type", "=", partner_type),
                        ("payment_type", "=", payment_type),
                    ]
                )
        return expression.OR(type_domains)

    def to_odoo_domain(self, env: api.Environment) -> list:
        domain = [("journal_id", "in", self._property_journal_ids(env))]
        if self.globalSearch:
            domain = expression.AND(
                [
                    domain,
                    [
                        "|",
                        "|",
                        ("ref", "ilike", self.globalSearch),
                        ("partner_id.display_name", "ilike", self.globalSearch),
                        ("create_uid.name", "ilike", self.globalSearch),
                    ],
                ]
            )
        if self.dateFrom and self.dateTo:
            domain = expression.AND(
                [
                    domain,
                    [
                        ("date", ">=", self.dateFrom),
                        ("date", "<=", self.dateTo),
                    ],
                ]
            )
        if self.paymentType:
            domain = expression.AND([domain, self._payment_type_domain()])
        if self.paymentMethod:
            domain = expression.AND(
                [domain, [("payment_method_line_id", "in", self.paymentMethod)]]
            )
        if self.reference:
            domain = expression.AND([domain, [("ref", "ilike", self.reference)]])
        if self.contact:
            domain = expression.AND(
                [domain, [("partner_id.display_name", "ilike", self.contact)]]
            )
        if self.createdBy:
            domain = expression.AND(
                [domain, [("create_uid.name", "ilike", self.createdBy)]]
            )
        if self.amountEq is not None:
            domain = expression.AND([domain, [("amount", "=", self.amountEq)]])
        if self.amountGt is not None:
            domain = expression.AND([domain, [("amount", ">", self.amountGt)]])
        if self.amountLt is not None:
            domain = expression.AND([domain, [("amount", "<", self.amountLt)]])
        return domain
