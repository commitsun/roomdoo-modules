from odoo import _, fields, models


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
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    note = fields.Text()

    def get_connector(self):
        """Return a connector instance for this vendor.

        Vendor-specific modules override this to dispatch on ``vendor_type``
        and instantiate their own connector.
        """
        self.ensure_one()
        raise NotImplementedError(
            _("No connector implementation for vendor '%s'.") % self.name
        )
