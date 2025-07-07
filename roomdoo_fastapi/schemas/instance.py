from pydantic import BaseModel, AnyHttpUrl


class Instance(BaseModel):
    name: str
    image: AnyHttpUrl
