import os

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class LockVendor(models.Model):
    _name = "lock.vendor"
    _description = "Lock Vendor"
    _order = "sequence, name"

    name = fields.Char(required=True)
    vendor_type = fields.Selection(
        selection=[],
        string="Type",
        required=True,
        help="Manufacturer connector used to communicate with the lock.",
    )
    pms_property_id = fields.Many2one(
        comodel_name="pms.property",
        string="Property",
        required=True,
        ondelete="restrict",
        help="Hotel this vendor configuration belongs to. "
        "Each property can hold its own set of credentials.",
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        related="pms_property_id.company_id",
        store=True,
        readonly=True,
    )
    pin_confirm_key = fields.Char(
        string="Confirm Key",
        help="Key the guest must press on the keypad after typing the PIN to "
        "validate it (e.g. '#' on most keypads). It is shown to the guest but "
        "is not part of the stored PIN. Defaults to the known key for the "
        "selected vendor; override it for unusual lock models.",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    note = fields.Text()

    @api.model
    def _pin_confirm_key_defaults(self):
        """Map ``vendor_type`` to the default keypad confirm key. Base knows
        no vendor types; each connector module adds its own entry, so future
        vendors (Salto '↵', Tessa '✓', …) only extend this hook."""
        return {}

    @api.onchange("vendor_type")
    def _onchange_vendor_type_pin_confirm_key(self):
        """Prefill the confirm key with the selected vendor's known default.
        The field stays editable for hotels with unusual lock models."""
        default = self._pin_confirm_key_defaults().get(self.vendor_type)
        if default:
            self.pin_confirm_key = default

    def get_connector(self):
        """Return a connector instance for this vendor.

        Vendor-specific modules override this to dispatch on ``vendor_type``
        and instantiate their own connector.
        """
        self.ensure_one()
        raise NotImplementedError(
            _("No connector implementation for vendor '%s'.") % self.name
        )

    def action_fetch_locks(self):
        """Fetch the vendor's locks and show them as plain text (name + device
        id, one per line) so the operator can read the ids needed to configure
        rooms. Read-only: it connects and lists, changing nothing."""
        self.ensure_one()
        connector = self.get_connector()
        try:
            locks = connector.list_locks()
        except NotImplementedError as exc:
            raise UserError(
                _("Vendor '%s' does not support listing locks yet.") % self.name
            ) from exc
        lines = [f"{lock.get('name') or ''}\t{lock.get('id') or ''}" for lock in locks]
        wizard = self.env["lock.list.wizard"].create(
            {
                "vendor_id": self.id,
                "lock_listing": "\n".join(lines) or _("No locks returned."),
            }
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Locks"),
            "res_model": "lock.list.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def _required_env(self, name):
        """Read a required environment variable holding a Roomdoo app
        credential (vendor ``client_id``/``client_secret``). These identify
        Roomdoo's integration — not the hotel — so they live in the
        deployment environment (``.docker/*.env``), never in the database.
        Raise a clear error when missing so a misconfigured instance fails
        loudly instead of authenticating with empty credentials."""
        self.ensure_one()
        value = os.environ.get(name)
        if not value:
            raise UserError(
                _(
                    "Missing environment variable '%(var)s' required by lock "
                    "vendor '%(vendor)s'. Set it in the deployment environment."
                )
                % {"var": name, "vendor": self.name}
            )
        return value
