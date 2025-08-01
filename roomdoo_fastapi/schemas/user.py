from pydantic import SecretStr

from odoo.addons.pms_fastapi.schemas.base import PmsBaseModel


class AvailabilityRuleField(PmsBaseModel):
    name: str


class ChangePasswordInput(PmsBaseModel):
    oldPassword: SecretStr
    newPassword: SecretStr


class UserEmailInput(PmsBaseModel):
    email: str


class ResetPasswordInput(PmsBaseModel):
    newPassword: SecretStr
    resetToken: SecretStr
