# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import _, api, models
from odoo.exceptions import UserError

VARIOUS_PARTNER_XMLID = "pms.various_pms_partner"

# Identity, contact and tax data that must never be overwritten on the
# system "various" partner used for simplified invoices. Any module that
# adds new identity-like fields to res.partner can extend this set by
# overriding ``_get_various_partner_protected_fields``.
_PROTECTED_FIELDS = frozenset(
    {
        # Core identity
        "name",
        "firstname",
        "lastname",
        "lastname2",
        "email",
        "phone",
        "mobile",
        "vat",
        "lang",
        "comment",
        "company_type",
        "is_company",
        # Address
        "street",
        "street2",
        "city",
        "zip",
        "state_id",
        "country_id",
        # Spanish AEAT identity (l10n_es_aeat)
        "aeat_identification",
        "aeat_identification_type",
        "aeat_anonymous_cash_customer",
        "aeat_simplified_invoice",
        "aeat_partner_name",
        "aeat_partner_vat",
        "aeat_partner_type",
        # Identification / personal documents
        "id_numbers",
        "document_number",
        "document_type",
        # PMS guest demographics
        "nationality_id",
        "gender",
        "birthdate_date",
        # Hierarchy
        "parent_id",
        "commercial_partner_id",
    }
)


class ResPartner(models.Model):
    _inherit = "res.partner"

    def _get_various_partner(self):
        return self.env.ref(VARIOUS_PARTNER_XMLID, raise_if_not_found=False)

    @api.model
    def _get_various_partner_protected_fields(self):
        """Return the set of field names that cannot be modified on the
        ``pms.various_pms_partner`` system contact.

        Filtered against ``self._fields`` so optional dependencies that
        don't install a given field (e.g. ``partner_firstname``) don't
        make the protection list reference unknown columns.
        """
        return {f for f in _PROTECTED_FIELDS if f in self._fields}

    def write(self, vals):
        various = self._get_various_partner()
        if various and various.id in self.ids:
            forbidden = self._get_various_partner_protected_fields().intersection(vals)
            if forbidden:
                raise UserError(
                    _(
                        "The system contact '%(name)s' is reserved for "
                        "simplified invoices and cannot be modified. "
                        "Blocked fields: %(fields)s. "
                        "If this is triggered from the REST API, create a "
                        "new contact for the guest instead of writing on "
                        "the anonymous one."
                    )
                    % {
                        "name": various.display_name,
                        "fields": ", ".join(sorted(forbidden)),
                    }
                )
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        various = self._get_various_partner()
        if various:
            various_id = various.id
            for vals in vals_list:
                if (
                    vals.get("parent_id") == various_id
                    or vals.get("commercial_partner_id") == various_id
                ):
                    raise UserError(
                        _(
                            "Cannot attach a child contact to the system "
                            "contact '%s'. This partner is reserved for "
                            "simplified invoices. Create the contact as an "
                            "independent partner instead."
                        )
                        % various.display_name
                    )
        return super().create(vals_list)
