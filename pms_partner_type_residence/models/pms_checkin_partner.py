from odoo import models


class PMSCheckinPartner(models.Model):
    _inherit = "pms.checkin.partner"

    def set_partner_address(self):
        """
        If the partner has a child of type 'residence', set the address fields
        (street, street2, zip, city, country_id, state_id) of the check-in partner
        to the address of that residence partner.
        If the partner has an adress but no residence, create the residence partner
        """
        for record in self:
            residence_vals = {
                "street": record.street,
                "street2": record.street2,
                "zip": record.zip,
                "city": record.city,
                "country_id": record.country_id.id,
                "state_id": record.state_id.id,
            }
            if any(residence_vals.values()):
                if record.partner_id:
                    address_fields = residence_vals.keys()
                    if not any(record.partner_id[field] for field in address_fields):
                        super(PMSCheckinPartner, record).set_partner_address()
                    else:
                        residence = record.partner_id.residence_partner_id
                        if residence and residence != record.partner_id:
                            residence.write(residence_vals)
                        else:
                            residence_vals.update(
                                {
                                    "parent_id": record.partner_id.id,
                                    "type": "residence",
                                }
                            )
                            self.env["res.partner"].create(residence_vals)
