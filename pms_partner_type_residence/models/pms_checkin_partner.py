from odoo import models


class PMSCheckinPartner(models.Model):
    _inherit = "pms.checkin.partner"

    def set_partner_address(self, residence_vals=None):
        """
        Sets the checkin.partner address in the associated partner
        or its residence child.

        - If partner has no residence address and the changes do not conflict with
          the partner's address: update partner address.
        - If partner has residence child: update residence with checkin values
        - If the changes conflict with partner's address:
          update residence if exists, else create residence child.
        """
        self.ensure_one()
        if not self.partner_id:
            return

        address_fields = {"street", "street2", "zip", "city", "country_id", "state_id"}
        if residence_vals is None:
            residence_vals = {
                field: self[field].id if hasattr(self[field], "id") else self[field]
                for field in address_fields
            }

        if not any(residence_vals.values()):
            return
        partner = self.partner_id

        address_fields_writed = residence_vals.keys()
        conflict_partner_address = any(
            self.partner_id[field]
            and self.partner_id[field] != residence_vals.get(field)
            for field in address_fields_writed
        )
        residence = self.partner_id.residence_partner_id
        if not conflict_partner_address and residence == self.partner_id:
            return super().set_partner_address(residence_vals)
        if residence and residence != self.partner_id:
            residence.write(residence_vals)
        else:
            # Establish the base fields, to preserve existing data
            partner_address = {
                field: (
                    partner[field].id
                    if hasattr(partner[field], "id")
                    else partner[field]
                )
                for field in address_fields
            }
            # Update with residence write values
            partner_address.update(residence_vals)

            partner_address.update(
                {
                    "parent_id": self.partner_id.id,
                    "type": "residence",
                }
            )
            self.env["res.partner"].create(partner_address)
