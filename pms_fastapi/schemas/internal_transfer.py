import datetime
from datetime import date

from pydantic import Field

from .base import CurrencyAmount, PmsBaseModel
from .payment import PaymentBase
from .payment_method import PaymentMethodSummary


class InternalTransferDetail(PaymentBase):
    """An internal transfer in detail: money moving between two payment methods.

    It has no contact and nothing to settle, so it carries neither `partner`,
    `folio`, `invoices` nor `availableRefundAmount`; instead it reports both ends
    of the movement.
    """

    originPaymentMethod: PaymentMethodSummary | None = Field(
        None, description="Payment method the money left from."
    )
    destinationPaymentMethod: PaymentMethodSummary | None = Field(
        None, description="Payment method the money went to."
    )

    @classmethod
    def from_account_payment(cls, payment):
        """Build from the origin (outbound) leg of the transfer."""
        data = cls._base_data(payment)
        if payment.payment_method_line_id:
            data[
                "originPaymentMethod"
            ] = PaymentMethodSummary.from_account_payment_method_line(
                payment.payment_method_line_id
            )
        counterpart = payment.paired_internal_transfer_payment_id
        if counterpart.payment_method_line_id:
            data[
                "destinationPaymentMethod"
            ] = PaymentMethodSummary.from_account_payment_method_line(
                counterpart.payment_method_line_id
            )
        return cls(**data)


class InternalTransferInput(PmsBaseModel):
    """Request body of POST /internal-transfers."""

    amount: CurrencyAmount = Field(gt=0, description="Always positive; > 0.")
    date: date
    originPaymentMethodId: int = Field(
        description="Payment method the money leaves from. Must be an outbound "
        "method of a different account than the destination one."
    )
    destinationPaymentMethodId: int = Field(
        description="Payment method the money goes to. Must be an inbound method."
    )
    reference: str = Field("", description="Reason for the transfer.")


class InternalTransferUpdate(PmsBaseModel):
    """Partial edit of a registered internal transfer.

    Only the modified fields are sent; omitted fields are left untouched. The
    origin and destination payment methods cannot be edited: they determine the
    two accounting entries of the transfer, so changing them means cancelling the
    transfer and registering a new one.
    """

    amount: CurrencyAmount | None = Field(
        None, gt=0, description="New amount. Always positive; > 0 (422 otherwise)."
    )
    # `datetime.date` (not the bare `date` name) to avoid the field name
    # shadowing the type when the default value is assigned.
    date: datetime.date | None = Field(None, description="New transfer date.")
    reference: str | None = Field(None, description="New reason for the transfer.")
