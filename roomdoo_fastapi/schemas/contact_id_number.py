from pydantic import Field

from odoo.addons.pms_fastapi.schemas import contact_id_number


class ContactIdNumberCategoryId(
    contact_id_number.ContactIdNumberCategoryId, extends=True
):
    """Schema for contact ID number category with short code."""

    shortCode: str = Field("", description="Short code for the ID category")

    @classmethod
    def from_res_partner_id_number_category(cls, id_number_category):
        category_record = super().from_res_partner_id_number_category(
            id_number_category
        )
        category_record.shortCode = id_number_category.short_code or ""
        return category_record


class ContactIdNumberCategorySummary(
    contact_id_number.ContactIdNumberCategorySummary, extends=True
):
    """Schema for contact ID number category summary with short code."""

    shortCode: str = Field("", description="Short code for the ID category")

    @classmethod
    def from_res_partner_id_number_category(cls, id_number_category):
        category_record = super().from_res_partner_id_number_category(
            id_number_category
        )
        category_record.shortCode = id_number_category.short_code or ""
        return category_record
