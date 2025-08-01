from .base import PmsBaseModel


class Language(PmsBaseModel):
    id: int
    name: str
    code: str

    @classmethod
    def from_res_lang(cls, lang):
        return cls(id=lang.id, name=lang.name, code=lang.code)
