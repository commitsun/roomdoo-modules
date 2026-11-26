from odoo import _, models


def find_opposite_relationship(relationship_code):
    inverse_relationships = {
        "PM": "HJ",
        "HJ": "PM",
        "TU": "OT",
        "TI": "SB",
        "HR": "HR",
        "AB": "NI",
        "NI": "AB",
        "BA": "BN",
        "BN": "BA",
        "CD": "CD",
        "CY": "CY",
        "SB": "TI",
        "SG": "YN",
        "YN": "SG",
        "OT": "OT",
    }
    return inverse_relationships.get(relationship_code, "OT")


class PmsApiCheckinRouterHelper(models.AbstractModel):
    _inherit = "pms_api_checkin.checkin_router.helper"

    def _partner_detail_values(self, cp):
        values = super()._partner_detail_values(cp)
        values.update(
            {
                "documentSupportNumber": cp.support_number or None,
                # Stored from the guest's own perspective, exchanged from the
                # responsible adult's one.
                "relationship": find_opposite_relationship(cp.ses_partners_relationship)
                if cp.ses_partners_relationship
                else None,
            }
        )
        return values

    def _responsible_field(self):
        # Spain keeps the responsible adult in the field of its traveller
        # report.
        return "ses_related_checkin_partner_id"

    def _context_values(self, folio):
        values = super()._context_values(folio)
        values["unaccompaniedMinors"] = bool(folio.ses_unaccompanied_minors)
        return values

    def _scan_result_values(self, result):
        values = super()._scan_result_values(result)
        if result.get("document_support_number"):
            values["documentSupportNumber"] = result["document_support_number"]
        return values

    def _state_is_stale(self, cp, country):
        # A Spanish resident needs the state, so the stored one is kept and
        # left to the validation of the guest.
        return super()._state_is_stale(cp, country) and country != self.env.ref(
            "base.es"
        )

    def _prepare_extra_vals(self, folio, cp, data, vals):
        super()._prepare_extra_vals(folio, cp, data, vals)
        if "documentSupportNumber" in data:
            vals["support_number"] = data["documentSupportNumber"] or False
        if "relationship" in data:
            relationship = data["relationship"]
            valid_codes = dict(cp._fields["ses_partners_relationship"].selection)
            if relationship and relationship not in valid_codes:
                self._raise_problem(
                    422,
                    "/errors/unknown-relationship",
                    _("Unknown relationship"),
                    _("The relationship code is not valid."),
                    field="relationship",
                )
            # Stored from the guest's own perspective.
            vals["ses_partners_relationship"] = (
                find_opposite_relationship(relationship) if relationship else False
            )
