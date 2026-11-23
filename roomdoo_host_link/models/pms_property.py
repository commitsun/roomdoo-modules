from odoo import api, models

HOST_FIELDS = {"name", "pms_property_code", "email", "ref"}


class PmsProperty(models.Model):
    _inherit = "pms.property"

    @api.model_create_multi
    def create(self, vals_list):
        properties = super().create(vals_list)
        link = self.env["roomdoo.host.link"]
        link._notify("properties", link._property_data(properties))
        return properties

    def write(self, vals):
        result = super().write(vals)
        if HOST_FIELDS & set(vals):
            link = self.env["roomdoo.host.link"]
            link._notify("properties", link._property_data(self))
        return result

    def unlink(self):
        gone = [{"id": prop.id, "deleted": True} for prop in self]
        result = super().unlink()
        self.env["roomdoo.host.link"]._notify("properties", gone)
        return result
