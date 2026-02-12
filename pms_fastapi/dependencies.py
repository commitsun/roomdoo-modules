from enum import Enum
from typing import Annotated

from fastapi import Depends, HTTPException, Query

from odoo.api import Environment

from odoo.addons.fastapi.dependencies import odoo_env
from odoo.addons.fastapi_auth_jwt.dependencies import AuthJwtOdooEnv

PublicEnv = Annotated[Environment, Depends(odoo_env)]
AuthenticatedEnv = Annotated[
    Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))
]


def create_order_dependency(
    field_enum: type[Enum],
    field_mapping: dict[str, str],
    default_fields: list[str] = None,
):
    """
    Create a specific ordering dependency for each endpoint
    """
    if default_fields is None:
        default_fields = [list(field_enum)[0].value]

    available_fields = [f.value for f in field_enum]

    example_desc = f"-{available_fields[0]}"
    if len(available_fields) > 1:
        example_desc += f",{available_fields[1]}"

    description = f"""Fields to sort the results by.

**Format:**
- Simple field: `{available_fields[0]}`
- Descending: `-{available_fields[0]}`
- Multiple fields: `{example_desc}`

**Available fields:**
{chr(10).join(f'- `{field}`' for field in available_fields)}

**Examples:**
- `{available_fields[0]}` - Sort by {available_fields[0]} ascending
- `-{available_fields[0]}` - Sort by {available_fields[0]} descending
- `{example_desc}` - Sort by {available_fields[0]} desc, \
    then {available_fields[1] if len(available_fields) > 1 else available_fields[0]} asc
    """

    def order_dependency(
        orderBy: Annotated[list[str], Query(description=description)] = default_fields
    ) -> str:
        valid_fields = set(f.value for f in field_enum)
        result = []
        if len(orderBy) == 1 and "," in orderBy[0]:
            orderBy = [f.strip() for f in orderBy[0].split(",")]

        for field in orderBy:
            direction = "asc"
            if field.startswith("-"):
                direction = "desc"
                field = field[1:]

            if field not in valid_fields:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid order field: {field}. "
                    f"Available fields: {', '.join(valid_fields)}",
                )

            odoo_field = field_mapping[field]
            result.append(f"{odoo_field} {direction}")

        return ", ".join(result)

    return order_dependency
