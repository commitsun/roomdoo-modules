from odoo import models

# Context key of the guest writes that come from the customer portal.
PORTAL_CHECKIN_CONTEXT = "customer_portal_checkin"


class PmsCheckinPartner(models.Model):
    _inherit = "pms.checkin.partner"

    def set_partner_id(self):
        """Never link a guest written from the portal to the contact that
        holds its document.

        A link holder could otherwise type the document number of someone else
        and read that contact's data back from the guest. Such a guest keeps
        its data but is left without a contact, neither linked nor given a
        duplicate one, for the reception to link it. The other guests get
        their contact as usual.
        """
        if not self.env.context.get(PORTAL_CHECKIN_CONTEXT):
            return super().set_partner_id()
        known = self.filtered(
            lambda guest: (
                not guest.partner_id
                and guest.document_number
                and guest.document_type
                and guest._get_partner_by_document(
                    guest.document_number, guest.document_type
                )
            )
        )
        return super(PmsCheckinPartner, self - known).set_partner_id()
