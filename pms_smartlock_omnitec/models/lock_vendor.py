from roomdoo_locks_omnitec import GatewayCommandError, OmnitecProvider

from odoo import api, fields, models

# Delays before retrying a sync after errcode -1 (the lock did not complete a
# command relayed by its gateway: it did not answer in time or was busy),
# indexed by the attempt that failed. Omnitec advises retrying after about ten
# seconds two or three times; the later retries are spaced so a sync still
# gets the same five attempts over some twenty minutes as with other errors.
_GATEWAY_COMMAND_RETRY_SECONDS = (10, 10, 600, 600)


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

    def _sync_retry_seconds(self, exc, attempt):
        if self.vendor_type == "omnitec" and isinstance(exc, GatewayCommandError):
            delays = _GATEWAY_COMMAND_RETRY_SECONDS
            return delays[min(attempt, len(delays)) - 1]
        return super()._sync_retry_seconds(exc, attempt)

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
            # Only the create path passes the credential: its passcodes are
            # tagged after it, so a grant interrupted halfway is resumed by the
            # next attempt instead of programmed twice.
            code = self.env.context.get("smartlock_grant_code")
            return OmnitecProvider(
                clientId=self._required_env(client_id_var),
                clientSecret=self._required_env(client_secret_var),
                username=self.omnitec_username,
                password=self.omnitec_password,
                passcode_tag=code._omnitec_passcode_tag() if code else None,
            )
        return super().get_connector()
