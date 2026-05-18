from pydantic import Field

from odoo.addons.pms_fastapi.schemas import pms_property


class PropertySummaryLongStay(pms_property.PropertySummary, extends=True):
    # Both are Selection fields on pms.property; snake_case names match the
    # Odoo fields so they are auto-mapped by ``_read_odoo_record()`` (via
    # ``parse_common_fields``).
    week_start_day: str | None = Field(None, alias="weekStartDay")
    long_stay_billing_timing: str | None = Field(
        None, alias="longStayBillingTiming"
    )
