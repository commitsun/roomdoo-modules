from odoo import api, models

HOST_FIELDS = {"name", "login", "email", "active", "groups_id"}


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model_create_multi
    def create(self, vals_list):
        users = super().create(vals_list)
        link = self.env["roomdoo.host.link"]
        data = link._user_data(users)
        if data:
            link._notify("users", data)
        return users

    def write(self, vals):
        result = super().write(vals)
        if HOST_FIELDS & set(vals):
            link = self.env["roomdoo.host.link"]
            data = link._user_data(self)
            if data:
                link._notify("users", data)
        return result
