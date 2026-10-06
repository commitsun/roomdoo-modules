import datetime
from datetime import date

from pydantic import Field

from .base import CurrencyAmount, PmsBaseModel
from .contact import ContactId
from .payment import PaymentBase, ReconciledInvoice
from .payment_method import PaymentMethodSummary
from .pms_folio import FolioId


class RefundBreakdownLine(PmsBaseModel):
    """One line of the refund breakdown of a payment: how much was refunded and
    against which counterpart payment.

    The counterpart is the other side of the operation: on a customer payment it
    is the refund that consumed part of it; on a refund it is one of the original
    payments it covers.
    """

    paymentId: int = Field(description="Id of the counterpart payment.")
    paymentName: str = ""
    amount: CurrencyAmount = Field(
        0.0, description="Refunded amount for this line. Always positive."
    )
    isReconciled: bool = Field(
        False,
        description="False when the original payment was already settled and the "
        "refund could not be matched against it automatically.",
    )

    @classmethod
    def from_pms_payment_refund_line(cls, refund_line, counterpart):
        return cls(
            **{
                "paymentId": counterpart.id,
                "paymentName": counterpart.name or "",
                "amount": refund_line.amount,
                "isReconciled": refund_line.is_reconciled,
                "_decimal_places": refund_line.currency_id.decimal_places,
            }
        )


class CustomerPaymentDetail(PaymentBase):
    """A customer payment or refund in detail. `paymentType` tells them apart."""

    partner: ContactId | None = None
    folio: FolioId | None = None
    paymentMethod: PaymentMethodSummary | None = None
    availableRefundAmount: CurrencyAmount = Field(
        0.0,
        description="Remaining amount available to refund: original amount minus "
        "previous refunds. Always positive or 0; 0 on a refund.",
    )
    invoices: list[ReconciledInvoice] = Field(
        default_factory=list,
        description="Invoices and credit notes this payment is settled against.",
    )
    refundBreakdown: list[RefundBreakdownLine] = Field(
        default_factory=list,
        description="On a payment, the refunds that consumed part of it. On a "
        "refund, the payments it covers and how much of each.",
    )

    @classmethod
    def from_account_payment(cls, payment):
        data = cls._base_data(payment)
        data["availableRefundAmount"] = payment.available_refund_amount
        if payment.partner_id:
            data["partner"] = ContactId.from_res_partner(payment.partner_id)
        if payment.folio_ids:
            data["folio"] = FolioId.from_pms_folio(payment.folio_ids[:1])
        if payment.payment_method_line_id:
            data[
                "paymentMethod"
            ] = PaymentMethodSummary.from_account_payment_method_line(
                payment.payment_method_line_id
            )
        if payment.reconciled_invoice_ids:
            data["invoices"] = [
                ReconciledInvoice.from_account_move(move)
                for move in payment.reconciled_invoice_ids
            ]
        data["refundBreakdown"] = [
            RefundBreakdownLine.from_pms_payment_refund_line(
                line, line.origin_payment_id
            )
            for line in payment.refund_breakdown_ids
        ] + [
            RefundBreakdownLine.from_pms_payment_refund_line(
                line, line.refund_payment_id
            )
            for line in payment.origin_refund_line_ids
        ]
        return cls(**data)


class CustomerPaymentInput(PmsBaseModel):
    """Request body of POST /customer-payments."""

    amount: CurrencyAmount = Field(gt=0, description="Always positive; > 0.")
    date: date
    paymentMethodId: int = Field(
        description="Payment method id (the front's 'payment mode'). Must be an "
        "inbound method."
    )
    partnerId: int | None = Field(
        None,
        description="May be null, in which case it is taken from the folio or "
        "invoice context.",
    )
    folioId: int | None = Field(
        None, description="Folio context. Mutually exclusive with invoiceId."
    )
    invoiceId: int | None = Field(
        None, description="Invoice context. Mutually exclusive with folioId."
    )
    reference: str = ""


class CustomerPaymentUpdate(PmsBaseModel):
    """Partial edit of a registered customer payment or refund.

    Only the modified fields are sent; omitted fields are left untouched. The
    payment method cannot be edited: the money has already entered a given
    account and moving it to another one is not an edit but a cancellation plus a
    new payment.
    """

    amount: CurrencyAmount | None = Field(
        None, gt=0, description="New amount. Always positive; > 0 (422 otherwise)."
    )
    # `datetime.date` (not the bare `date` name) to avoid the field name
    # shadowing the type when the default value is assigned.
    date: datetime.date | None = Field(None, description="New payment date.")
    partnerId: int | None = Field(None, description="New contact of the payment.")
    reference: str | None = Field(None, description="New reference.")


class PaymentRefundLine(PmsBaseModel):
    """One line of a refund operation: how much to refund from a given payment."""

    paymentId: int = Field(description="Id of the original customer payment to refund.")
    amount: CurrencyAmount = Field(
        gt=0,
        description="Amount to refund from this payment. Always positive; > 0.",
    )


class PaymentRefundInput(PmsBaseModel):
    """Request body of POST /customer-payments/refunds.

    One operation = one single refund (one date + one method + one total amount)
    covering N payments of the same folio.
    """

    # `datetime.date` (not the bare `date` name) to avoid the field name
    # shadowing the type when a Field default is assigned.
    date: datetime.date = Field(
        description="Refund date. Applies to the whole operation."
    )
    paymentMethodId: int = Field(
        description="Payment method id (the front's 'refund mode'). Must be an "
        "outbound method."
    )
    payments: list[PaymentRefundLine] = Field(
        min_length=1,
        description="Payments to refund with the amount per payment. Minimum 1 "
        "item; all payments must belong to the same folio.",
    )
