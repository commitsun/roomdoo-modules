from odoo import models


class ResPartner(models.Model):
    _name = "res.partner"
    _inherit = ["mail.thread.phone", "res.partner"]

    def _phone_get_number_fields(self):
        """This method returns the fields to use to find the number to use to
        send an SMS on a record."""
        return ["mobile", "phone"]
