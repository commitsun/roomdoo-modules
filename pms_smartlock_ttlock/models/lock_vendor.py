from roomdoo_locks_ttlock import TTLockProvider

from odoo import fields, models


class LockVendor(models.Model):
    _inherit = "lock.vendor"

    vendor_type = fields.Selection(
        selection_add=[("ttlock", "TTLock")],
        ondelete={"ttlock": "cascade"},
    )
    ttlock_client_id = fields.Char(string="TTLock Client ID")
    ttlock_client_secret = fields.Char(string="TTLock Client Secret")
    ttlock_username = fields.Char(string="TTLock Username")
    ttlock_password = fields.Char(string="TTLock Password")

    def get_connector(self):
        self.ensure_one()
        if self.vendor_type == "ttlock":
            return TTLockProvider(
                clientId=self.ttlock_client_id,
                clientSecret=self.ttlock_client_secret,
                username=self.ttlock_username,
                password=self.ttlock_password,
            )
        return super().get_connector()
