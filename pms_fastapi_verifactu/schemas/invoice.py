from enum import Enum

from pydantic import Field

from odoo.addons.pms_fastapi.schemas import invoice

AEAT_STATE_TO_CAMEL = {
    "not_sent": "notSent",
    "sent": "sent",
    "sent_w_errors": "sentWithErrors",
    "incorrect": "incorrect",
    "cancel": "cancel",
    "cancel_w_errors": "cancelWithErrors",
    "cancel_incorrect": "cancelIncorrect",
}


class VerifactuStateEnum(str, Enum):
    notSent = "notSent"
    sent = "sent"
    sentWithErrors = "sentWithErrors"
    incorrect = "incorrect"
    cancel = "cancel"
    cancelWithErrors = "cancelWithErrors"
    cancelIncorrect = "cancelIncorrect"


class InvoiceSummary(invoice.InvoiceSummary, extends=True):
    verifactuState: VerifactuStateEnum | None = Field(None, alias="verifactuState")

    @classmethod
    def from_account_move(cls, account_move):
        res = super().from_account_move(account_move)
        if account_move.verifactu_enabled and account_move.aeat_state:
            camel_value = AEAT_STATE_TO_CAMEL.get(account_move.aeat_state)
            if camel_value:
                res.verifactuState = VerifactuStateEnum(camel_value)
        return res
