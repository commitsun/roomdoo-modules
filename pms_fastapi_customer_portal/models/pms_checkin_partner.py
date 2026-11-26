import logging

from odoo import fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# Context key of the guest writes that come from the customer portal.
PORTAL_CHECKIN_CONTEXT = "customer_portal_checkin"


class PmsCheckinPartner(models.Model):
    _inherit = "pms.checkin.partner"

    # Counted with SQL by the portal: no default, so a NULL counts as none.
    portal_document_scan_count = fields.Integer(
        string="Portal document scans",
        readonly=True,
        copy=False,
        help="Identity documents scanned for this guest from the customer "
        "portal, read or not. The portal scans no more once the limit is "
        "reached.",
    )

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
            lambda guest: not guest.partner_id and guest._document_held_by_a_contact()
        )
        return super(PmsCheckinPartner, self - known).set_partner_id()

    def _document_held_by_a_contact(self):
        """Whether the document lookup of the guest would find a contact.

        Same search as ``_get_partner_by_document``, which takes ``%`` and
        ``_`` as wildcards, but it does not expect a number to match the
        documents of several contacts: it would fail, and the answer would
        tell the link holder so.
        """
        self.ensure_one()
        if not self.document_number or not self.document_type:
            return False
        return bool(
            self.env["res.partner.id_number"]
            .sudo()
            .search(
                [
                    ("name", "=ilike", self.document_number),
                    ("category_id", "=", self.document_type.id),
                ],
                limit=1,
            )
        )

    def _create_or_update_partner_document(self):
        """Copy the document of a guest written from the portal to its contact
        only when no other contact holds it.

        The identification numbers of the contacts are unique, and the check
        takes ``%`` and ``_`` as wildcards, so the copy would otherwise reject
        the write whenever another contact holds the number, and the answer
        would tell a link holder whose numbers exist, or extract them a
        character at a time. Such a guest keeps the document it was given and
        its contact the one it had, for the reception to sort out.
        """
        if not self.env.context.get(PORTAL_CHECKIN_CONTEXT):
            return super()._create_or_update_partner_document()
        for guest in self:
            try:
                with self.env.cr.savepoint():
                    super(PmsCheckinPartner, guest)._create_or_update_partner_document()
            except ValidationError as error:
                _logger.info(
                    "Document of portal guest %s not copied to its contact: %s",
                    guest.id,
                    error.args[0] if error.args else error,
                )
