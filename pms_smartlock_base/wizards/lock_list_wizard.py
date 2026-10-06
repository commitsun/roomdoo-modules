from odoo import fields, models


class LockListWizard(models.TransientModel):
    _name = "lock.list.wizard"
    _description = "Smartlock Listing"

    vendor_id = fields.Many2one(
        comodel_name="lock.vendor",
        readonly=True,
    )
    lock_listing = fields.Text(
        string="Locks",
        readonly=True,
        help="Plain-text list of the vendor's locks (name and device id, one "
        "per line) so the operator can read the ids needed to configure rooms.",
    )
