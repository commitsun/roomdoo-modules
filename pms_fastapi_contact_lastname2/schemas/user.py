from odoo.addons.pms_fastapi.schemas import user


class UserLastname2(user.User, extends=True):
    lastname2: str = ""

    @classmethod
    def from_res_users(cls, user_record) -> dict:
        res = super().from_res_users(user_record)
        res.lastname2 = user_record.lastname2 or ""
        return res
