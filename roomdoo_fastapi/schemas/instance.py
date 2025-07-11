from pydantic import AnyHttpUrl, BaseModel


class Instance(BaseModel):
    name: str
    image: AnyHttpUrl
