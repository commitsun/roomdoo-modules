from roomdoo_locks_omnitec import OmnitecProvider

from odoo import fields, models


class LockVendor(models.Model):
    _inherit = "lock.vendor"

    vendor_type = fields.Selection(
        selection_add=[("omnitec", "Omnitec / Rent&Pass")],
        ondelete={"omnitec": "cascade"},
    )
    omnitec_client_id = fields.Char(string="Omnitec Client ID")
    omnitec_client_secret = fields.Char(string="Omnitec Client Secret")
    omnitec_username = fields.Char(string="Omnitec Username")
    omnitec_password = fields.Char(string="Omnitec Password")

    def get_connector(self):
        self.ensure_one()
        if self.vendor_type == "omnitec":
            return OmnitecProvider(
                clientId=self.omnitec_client_id,
                clientSecret=self.omnitec_client_secret,
                username=self.omnitec_username,
                password=self.omnitec_password,
            )
        return super().get_connector()
