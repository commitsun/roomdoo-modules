from typing import Annotated

from fastapi import Depends

from odoo import models
from odoo.api import Environment

from odoo.addons.fastapi_auth_jwt.dependencies import AuthJwtOdooEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.contact import (
    ContactInsert,
    ContactUpdate,
)


@pms_api_router.get(
    "/contacts-count",
    response_model=int,
    tags=["contact"],
)
async def count_contacts(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
) -> int:
    return env["pms_api_contact.contact_router.helper"].new().count()


class PmsApiContactRouterHelper(models.AbstractModel):
    _inherit = "pms_api_contact.contact_router.helper"

    def create_contact(self, data: ContactInsert):
        res = super().create_contact(data)
        if not data.is_same_address:
            address_vals = {
                "street": data.residenceStreet,
                "street2": data.residenceStreet2,
                "zip": data.residenceZip,
                "city": data.residenceCity,
                "state_id": data.residenceState if data.residenceState else False,
                "country_id": data.residenceCountry if data.residenceCountry else False,
                "parent_id": res.id,
                "type": "residence",
            }
            self.env["res.partner"].sudo().create(address_vals)
        return res

    def update_contact(self, data: ContactUpdate, contact_id: int):
        res = super().update_contact(data, contact_id)
        partner = self.env["res.partner"].sudo().browse(contact_id)
        if partner.residence_partner_id == partner:
            if not data.is_same_address:
                address_vals = {
                    "street": data.residenceStreet,
                    "street2": data.residenceStreet2,
                    "zip": data.residenceZip,
                    "city": data.residenceCity,
                    "state_id": data.residenceState if data.residenceState else False,
                    "country_id": data.residenceCountry
                    if data.residenceCountry
                    else False,
                    "parent_id": partner.id,
                    "type": "residence",
                }
                self.env["res.partner"].sudo().create(address_vals)
        else:
            data_dump = data.model_dump(exclude_unset=True)
            residence_vals = {}
            if "residenceStreet" in data_dump:
                residence_vals["street"] = data.residenceStreet
            if "residenceStreet2" in data_dump:
                residence_vals["street2"] = data.residenceStreet2
            if "residenceZip" in data_dump:
                residence_vals["zip"] = data.residenceZip
            if "residenceCity" in data_dump:
                residence_vals["city"] = data.residenceCity
            if "residenceState" in data_dump:
                residence_vals["state_id"] = data.residenceState
            if "residenceCountry" in data_dump:
                residence_vals["country_id"] = data.residenceCountry
            if residence_vals:
                partner.residence_partner_id.write(residence_vals)
        return res
