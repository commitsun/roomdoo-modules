from enum import Enum

from pydantic import Field

from odoo.addons.pms_fastapi.schemas import invoice

AEAT_STATE_TO_API = {
    "not_sent": "notSent",
    "sent": "correct",
    "sent_w_errors": "error",
    "incorrect": "error",
    "cancel": "correct",
    "cancel_w_errors": "error",
    "cancel_incorrect": "error",
}


class VerifactuStateEnum(str, Enum):
    notSent = "notSent"
    correct = "correct"
    error = "error"


class InvoiceSummary(invoice.InvoiceSummary, extends=True):
    verifactuState: VerifactuStateEnum | None = Field(None, alias="verifactuState")
    verifactuMessage: str | None = None

    @classmethod
    def from_account_move(cls, account_move):
        res = super().from_account_move(account_move)
        if account_move.verifactu_enabled and account_move.aeat_state:
            camel_value = AEAT_STATE_TO_API.get(account_move.aeat_state)
            if camel_value:
                res.verifactuState = VerifactuStateEnum(camel_value)
        if account_move.verifactu_enabled and account_move.aeat_send_error:
            res.verifactuMessage = account_move.aeat_send_error
        return res
