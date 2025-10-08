from pydantic import Field

from odoo.addons.pms_fastapi.schemas import user


class UserLastname2(user.User, extends=True):
    lastname2: str = Field("", alias="lastname2")


class UserUpdate(user.UserUpdate, extends=True):
    lastname2: str | None = Field(None, alias="lastname2")
