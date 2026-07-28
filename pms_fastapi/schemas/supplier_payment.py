import datetime
from datetime import date

from pydantic import Field

from .base import CurrencyAmount, PmsBaseModel
from .contact import ContactId
from .payment import PaymentBase, ReconciledInvoice
from .payment_method import PaymentMethodSummary


class SupplierPaymentDetail(PaymentBase):
    """A supplier payment or refund in detail. `paymentType` tells them apart."""

    partner: ContactId | None = None
    paymentMethod: PaymentMethodSummary | None = None
    bills: list[ReconciledInvoice] = Field(
        default_factory=list,
        description="Vendor bills and credit notes this payment is settled against.",
    )

    @classmethod
    def from_account_payment(cls, payment):
        data = cls._base_data(payment)
        if payment.partner_id:
            data["partner"] = ContactId.from_res_partner(payment.partner_id)
        if payment.payment_method_line_id:
            data[
                "paymentMethod"
            ] = PaymentMethodSummary.from_account_payment_method_line(
                payment.payment_method_line_id
            )
        if payment.reconciled_bill_ids:
            data["bills"] = [
                ReconciledInvoice.from_account_move(move)
                for move in payment.reconciled_bill_ids
            ]
        return cls(**data)


class SupplierPaymentInput(PmsBaseModel):
    """Request body of POST /supplier-payments."""

    amount: CurrencyAmount = Field(gt=0, description="Always positive; > 0.")
    date: date
    paymentMethodId: int = Field(
        description="Payment method id (the front's 'payment mode'). Must be an "
        "outbound method."
    )
    partnerId: int = Field(description="The supplier being paid.")
    reference: str = ""


class SupplierPaymentUpdate(PmsBaseModel):
    """Partial edit of a registered supplier payment or refund.

    Only the modified fields are sent; omitted fields are left untouched. The
    payment method cannot be edited: the money has already left a given account
    and moving it to another one is not an edit but a cancellation plus a new
    payment.
    """

    amount: CurrencyAmount | None = Field(
        None, gt=0, description="New amount. Always positive; > 0 (422 otherwise)."
    )
    # `datetime.date` (not the bare `date` name) to avoid the field name
    # shadowing the type when the default value is assigned.
    date: datetime.date | None = Field(None, description="New payment date.")
    partnerId: int | None = Field(None, description="New supplier of the payment.")
    reference: str | None = Field(None, description="New reference.")
