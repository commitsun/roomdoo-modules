from pydantic import BaseModel, Field


class BookaiBaseModel(BaseModel):
    pass


class BookaiTemplateParam(BookaiBaseModel):
    key: str
    description: str = ""
    value: str | None = None


class BookaiTemplateAvailability(BookaiBaseModel):
    id: int
    code: str
    name: str
    active: bool
    target_model_name: str
    bookai_template_code: str
    apply_domain: str = "[]"
    property_ids: list[int] = Field(default_factory=list)
    available_for_all_properties: bool = False
    body: str = ""
    params: list[BookaiTemplateParam] = Field(default_factory=list)
    body_rendered: str = ""

    @classmethod
    def from_notification_template(
        cls,
        template,
        param_values: dict | None = None,
        body_rendered: str = "",
    ):
        values = param_values or {}
        params = [
            BookaiTemplateParam(
                key=param.key or "",
                description=param.description or "",
                value=(
                    None
                    if param_values is None
                    else (
                        ""
                        if values.get(param.key) in (False, None)
                        else str(values.get(param.key))
                    )
                ),
            )
            for param in template.bookai_param_ids.sorted(lambda x: (x.sequence, x.id))
        ]
        property_ids = template.pms_property_ids.ids
        return cls(
            id=template.id,
            code=template.code or "",
            name=template.name or "",
            active=bool(template.active),
            target_model_name=template.target_model_name or "",
            bookai_template_code=template.bookai_template_code or "",
            apply_domain=template.apply_domain or "[]",
            property_ids=property_ids,
            available_for_all_properties=not bool(property_ids),
            body=template.body or "",
            params=params,
            body_rendered=body_rendered or "",
        )
