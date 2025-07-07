
from typing import Annotated
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.roomdoo_fastapi.schemas.user import AvailabilityRuleField
from odoo.api import Environment
from odoo.addons.fastapi_auth_jwt.dependencies import AuthJwtOdooEnv
from fastapi import Depends


@pms_api_router.get(
    '/user/availability-rule-field/',
    response_model=list[AvailabilityRuleField],
    tags=["user"]
)
async def get_availability_rule_fields(env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))]) -> list[AvailabilityRuleField]:
    """
        Get user availability rules fields for user interface.
    """
    user = env.user
    return  [
        AvailabilityRuleField(
            name=rule.name
        ) for rule in user.availability_rule_field_ids
    ]
