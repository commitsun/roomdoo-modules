from pydantic import SecretStr

from .base import PmsBaseModel


class TwoFactorSetup(PmsBaseModel):
    """What an authentication app needs to start generating codes."""

    uri: str
    secret: str


class TwoFactorActivation(PmsBaseModel):
    code: str
    password: SecretStr
