from pydantic import SecretStr

from .base import PmsBaseModel


class PmsLoginInput(PmsBaseModel):
    username: str
    password: SecretStr
