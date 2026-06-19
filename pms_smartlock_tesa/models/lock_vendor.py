from roomdoo_locks_tesa import TesaSmartairProvider

from odoo import api, fields, models


class LockVendor(models.Model):
    _inherit = "lock.vendor"

    vendor_type = fields.Selection(
        selection_add=[("tesa", "TESA Smartair")],
        ondelete={"tesa": "cascade"},
    )

    @api.model
    def _pin_confirm_key_defaults(self):
        res = super()._pin_confirm_key_defaults()
        # TESA Smartair keypads validate the PIN with the check key (✓, U+2713).
        res["tesa"] = "✓"
        return res

    tesa_host = fields.Char(
        string="TESA Host",
        help="Hostname or IP of the hotel's Smartair server, without scheme "
        "(e.g. 192.168.1.50). TESA has no cloud: Odoo connects to the hotel's "
        "own on-prem instance.",
    )
    tesa_port = fields.Integer(
        string="TESA Port",
        default=8181,
        help="Port of the Smartair server (default 8181).",
    )
    tesa_operator_name = fields.Char(string="TESA Operator")
    tesa_operator_password = fields.Char(string="TESA Operator Password")
    tesa_verify_ssl = fields.Boolean(
        string="Verify SSL",
        default=False,
        help="Require a valid server certificate. Off by default because "
        "on-prem Smartair servers usually present a self-signed certificate.",
    )

    def get_connector(self):
        self.ensure_one()
        if self.vendor_type == "tesa":
            # TESA Smartair has no cloud and no shared Roomdoo app credentials:
            # Odoo connects to the hotel's own server, so the host and operator
            # credentials live on the record, not in the environment.
            return TesaSmartairProvider(
                host=self.tesa_host,
                operator_name=self.tesa_operator_name,
                operator_password=self.tesa_operator_password,
                port=self.tesa_port or 8181,
                verify_ssl=self.tesa_verify_ssl,
            )
        return super().get_connector()
