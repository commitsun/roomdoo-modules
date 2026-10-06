from odoo import fields, models


class SaltoRole(models.Model):
    """A role available on a Salto KS site, fetched from the vendor.

    Role ids are unique per site and cannot be hardcoded, so the operator
    pulls the list with the vendor's "Fetch Salto roles" button and picks the
    guest role (the basic *User* role, which only opens doors). This model is
    just that cached picklist; ``lock.vendor.salto_role_id`` points at the
    chosen one and the connector is built with its ``salto_id``."""

    _name = "salto.role"
    _description = "Salto KS Role"
    _order = "name"

    vendor_id = fields.Many2one(
        comodel_name="lock.vendor",
        string="Vendor",
        required=True,
        ondelete="cascade",
        index=True,
    )
    salto_id = fields.Char(
        string="Salto Role ID",
        required=True,
        help="Identifier of the role on the Salto KS site.",
    )
    name = fields.Char()

    _sql_constraints = [
        (
            "vendor_salto_id_uniq",
            "unique(vendor_id, salto_id)",
            "This Salto role is already registered for the vendor.",
        ),
    ]

    def name_get(self):
        return [(role.id, role.name or role.salto_id) for role in self]
