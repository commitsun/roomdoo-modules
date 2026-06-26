from typing import Annotated, get_args, get_type_hints

from extendable_pydantic import StrictExtendableBaseModel
from pydantic import ConfigDict, model_validator

from odoo import _, api
from odoo.exceptions import AccessDenied
from odoo.tools.float_utils import json_float_round


class _CurrencyMarker:
    pass


CurrencyAmount = Annotated[float, _CurrencyMarker()]


# Minimum length a free-text search value must have before it is used to filter.
# Shorter (but non-empty) values are rejected; empty/whitespace means "no filter".
MIN_SEARCH_TEXT_LENGTH = 3


class _SearchTextMarker:
    pass


# Type alias to declaratively mark a search query param as a guarded free-text field.
# Annotate a *Search __init__ param with ``SearchText`` and the length guard applies
# automatically (see PmsApiRouter in models/fastapi_endpoint.py). The marker is inert
# for FastAPI, which only consumes FieldInfo/Query metadata.
SearchText = Annotated[str | None, _SearchTextMarker()]


class PmsBaseModel(StrictExtendableBaseModel):
    model_config = ConfigDict(populate_by_name=True)

    @staticmethod
    def url_image_pms_api_rest(env, model, record_id, field):
        # PmsBaseModel.pms_api_check_access(
        #     user=env.user, records=env[model].sudo().browse(record_id)
        # )
        rt_image_attach = (
            env["ir.attachment"]
            .sudo()
            .search(
                [
                    ("res_model", "=", model),
                    ("res_id", "=", record_id),
                    ("res_field", "=", field),
                ]
            )
        )
        if rt_image_attach:
            result = PmsBaseModel.get_attachment_url(env, rt_image_attach)
        else:
            result = False
        return result if result else ""

    @staticmethod
    def get_attachment_url(env, attachment):
        """
        Returns the URL of an attachment, generating an access token if necessary.
        """
        if not attachment.access_token:
            attachment.generate_access_token()
        return (
            env["ir.config_parameter"].sudo().get_param("web.base.url")
            + f"/web/image/{attachment.id}?access_token={attachment.access_token}"
        )

    @staticmethod
    def pms_api_check_access(user, records=False):
        if not records or user.has_group("base.group_public"):
            return
        # Property access check
        if records._name == "pms.property":
            record_ids = records.ids
            user_property_ids = user.pms_property_ids.ids
            if not all(record_id in user_property_ids for record_id in record_ids):
                properties_not_allowed = records.filtered(
                    lambda record: record.id not in user_property_ids
                )
                raise AccessDenied(
                    _("You are not allowed to access these properties. %s")
                    % properties_not_allowed.mapped("name")
                )
        elif hasattr(records, "pms_property_id"):
            pms_property_ids = [
                record.pms_property_id.id
                for record in records
                if record.pms_property_id
            ]
            if pms_property_ids:
                user_property_ids = user.pms_property_ids.ids
                if not any(
                    prop_id in user_property_ids for prop_id in pms_property_ids
                ):
                    properties_not_allowed = records.filtered(
                        lambda record: record.pms_property_id.id
                        not in user_property_ids
                    )
                    raise AccessDenied(
                        _("You are not allowed to access this properties. %s")
                        % properties_not_allowed.mapped("pms_property_id.name")
                    )
        elif hasattr(records, "pms_property_ids"):
            pms_property_ids = [
                prop_id for record in records for prop_id in record.pms_property_ids.ids
            ]
            if pms_property_ids:
                user_property_ids = user.pms_property_ids.ids
                if not any(
                    prop_id in user_property_ids for prop_id in pms_property_ids
                ):
                    properties_not_allowed = records.filtered(
                        lambda record: any(
                            prop_id not in user_property_ids
                            for prop_id in record.pms_property_ids.ids
                        )
                    )
                    raise AccessDenied(
                        _("You are not allowed to access this properties. %s")
                        % properties_not_allowed.mapped("pms_property_ids.name")
                    )

    @classmethod
    def _get_odoo_read_fields(cls, odoo_object) -> list[str]:
        odoo_available_fields = set(odoo_object._fields.keys())
        pydantic_fields = []
        for field_name in cls.model_fields.keys():
            pydantic_fields.append(field_name)
        valid_fields = [f for f in pydantic_fields if f in odoo_available_fields]
        return valid_fields

    @classmethod
    def _read_odoo_record(cls, odoo_object):
        fields_to_read = cls._get_odoo_read_fields(odoo_object)
        record = odoo_object.read(fields_to_read)[0]
        model_fields = cls.model_fields.keys()
        return {k: v for k, v in record.items() if v and k in model_fields}

    @model_validator(mode="before")
    @classmethod
    def _round_currency_fields(cls, data):
        precision = data.pop("_decimal_places", 2)
        for name, field in cls.model_fields.items():
            if any(isinstance(m, _CurrencyMarker) for m in field.metadata):
                if name in data and data[name] is not None:
                    data[name] = json_float_round(data[name], precision)
        return data


_search_text_fields_cache: dict[type, tuple[str, ...]] = {}


def _has_search_text_marker(annotation) -> bool:
    """Whether ``annotation`` carries the SearchText marker, anywhere inside.

    Recurses through Union/Optional and nested Annotated because get_type_hints
    wraps params defaulting to ``None`` in ``Optional[...]``, which hides the
    marker from the top-level ``__metadata__``.
    """
    if any(
        isinstance(meta, _SearchTextMarker)
        for meta in getattr(annotation, "__metadata__", ())
    ):
        return True
    return any(_has_search_text_marker(arg) for arg in get_args(annotation))


class BaseSearch:
    def to_odoo_domain(self, env: api.Environment) -> list:
        return []

    def to_odoo_context(self, env: api.Environment) -> dict:
        return {}

    @classmethod
    def _search_text_field_names(cls) -> tuple[str, ...]:
        """Names of the __init__ params marked as guarded free-text (``SearchText``)."""
        cached = _search_text_fields_cache.get(cls)
        if cached is None:
            hints = get_type_hints(cls.__init__, include_extras=True)
            cached = tuple(
                name for name, hint in hints.items() if _has_search_text_marker(hint)
            )
            _search_text_fields_cache[cls] = cached
        return cached

    def first_short_search_text(self) -> str | None:
        """First guarded text field whose value is non-empty but too short.

        Returns the offending param name, or None when every guarded field is
        empty/whitespace (no filter) or long enough.
        """
        for name in self._search_text_field_names():
            value = getattr(self, name, None)
            if value is None:
                continue
            stripped = value.strip()
            if stripped and len(stripped) < MIN_SEARCH_TEXT_LENGTH:
                return name
        return None
