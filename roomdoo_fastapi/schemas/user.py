from pydantic import BaseModel, SecretStr


class AvailabilityRuleField(BaseModel):
    name: str


class ChangePasswordInput(BaseModel):
    oldPassword: SecretStr
    newPassword: SecretStr


class UserEmailInput(BaseModel):
    email: str


class ResetPasswordInput(BaseModel):
    newPassword: SecretStr
    resetToken: SecretStr
