from roomdoo_locks_omnitec import OmnitecProvider

from odoo import api, fields, models


class LockVendor(models.Model):
    _inherit = "lock.vendor"

    vendor_type = fields.Selection(
        selection_add=[("omnitec", "Omnitec / Rent&Pass")],
        ondelete={"omnitec": "cascade"},
    )

    @api.model
    def _pin_confirm_key_defaults(self):
        res = super()._pin_confirm_key_defaults()
        res["omnitec"] = "#"
        return res

    omnitec_osaccess = fields.Selection(
        selection=[("modern", "OsAccess"), ("legacy", "OsAccess Legacy")],
        string="OsAccess Version",
        default="modern",
        help="OsAccess generation of this hotel's installation. Selects which "
        "Roomdoo app credentials (modern or legacy) are used to authenticate; "
        "the instance has both configured in the environment.",
    )
    omnitec_username = fields.Char(string="Omnitec Username")
    omnitec_password = fields.Char(string="Omnitec Password")

    def get_connector(self):
        self.ensure_one()
        if self.vendor_type == "omnitec":
            # client_id/secret identify Roomdoo's Omnitec app and live in the
            # environment. Omnitec needs a different app per OsAccess
            # generation, so the record's ``omnitec_osaccess`` picks the pair.
            if self.omnitec_osaccess == "legacy":
                client_id_var = "OMNITEC_LEGACY_CLIENT_ID"
                client_secret_var = "OMNITEC_LEGACY_CLIENT_SECRET"
            else:
                client_id_var = "OMNITEC_CLIENT_ID"
                client_secret_var = "OMNITEC_CLIENT_SECRET"
            return OmnitecProvider(
                clientId=self._required_env(client_id_var),
                clientSecret=self._required_env(client_secret_var),
                username=self.omnitec_username,
                password=self.omnitec_password,
            )
        return super().get_connector()
