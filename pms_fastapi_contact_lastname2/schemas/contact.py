from odoo.addons.pms_fastapi.schemas import contact


class ContactDetailLastname2(contact.ContactDetail, extends=True):
    lastname2: str = ""

    @classmethod
    def from_res_partner(cls, partner) -> dict:
        res = super().from_res_partner(partner)
        res.lastname2 = partner.lastname2 or ""
        return res
