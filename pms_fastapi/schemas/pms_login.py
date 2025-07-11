from pydantic import BaseModel, SecretStr


class PmsLoginInput(BaseModel):
    username: str
    password: SecretStr
