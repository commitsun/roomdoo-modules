from pydantic import AnyHttpUrl, BaseModel

from .pms_property import PropertyId


class User(BaseModel):
    id: int
    name: str
    firstName: str = ""
    lastName: str = ""
    lastName2: str = ""
    email: str = ""
    phone: str = ""
    image: AnyHttpUrl = ""
    defaultProperty: PropertyId
