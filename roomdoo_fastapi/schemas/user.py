from pydantic import Field, SecretStr

from odoo.addons.pms_fastapi.schemas import user as pms_user_schema
from odoo.addons.pms_fastapi.schemas.base import PmsBaseModel


class UserWithFeatureFlags(pms_user_schema.User, extends=True):
    featureFlags: list[str] = Field(default_factory=list)

    @classmethod
    def from_res_users(cls, user_record):
        user_instance = super().from_res_users(user_record)
        user_instance.featureFlags = (
            user_record.env["feature.flag"].sudo().get_active_for_user(user_record)
        )
        return user_instance


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
