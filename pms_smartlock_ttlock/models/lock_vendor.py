from roomdoo_locks_ttlock import TTLockProvider

from odoo import fields, models


class LockVendor(models.Model):
    _inherit = "lock.vendor"

    vendor_type = fields.Selection(
        selection_add=[("ttlock", "TTLock")],
        ondelete={"ttlock": "cascade"},
    )
    ttlock_username = fields.Char(string="TTLock Username")
    ttlock_password = fields.Char(string="TTLock Password")

    def get_connector(self):
        self.ensure_one()
        if self.vendor_type == "ttlock":
            # client_id/secret identify Roomdoo's TTLock app (shared across
            # hotels) and live in the environment, not the database.
            return TTLockProvider(
                clientId=self._required_env("TTLOCK_CLIENT_ID"),
                clientSecret=self._required_env("TTLOCK_CLIENT_SECRET"),
                username=self.ttlock_username,
                password=self.ttlock_password,
            )
        return super().get_connector()
