from typing import Annotated

from extendable_pydantic import StrictExtendableBaseModel
from pydantic import ConfigDict, model_validator

from odoo import _, api
from odoo.exceptions import AccessDenied
from odoo.tools.float_utils import json_float_round


class _CurrencyMarker:
    pass


CurrencyAmount = Annotated[float, _CurrencyMarker()]


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


class BaseSearch:
    def to_odoo_domain(self, env: api.Environment) -> list:
        return []

    def to_odoo_context(self, env: api.Environment) -> dict:
        return {}
