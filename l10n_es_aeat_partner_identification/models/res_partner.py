from odoo import _, api, models
from odoo.exceptions import ValidationError

AEAT_TYPES_ID_CATEGORY_MAP = {
    "03": "passport",
    "05": "residential_certificate",
    "06": "another_document",
}


class ResPartner(models.Model):
    _inherit = "res.partner"

    @api.model
    def get_duplicate_aeat(self, aeat_identification_type, aeat_identification):
        identification_types_aeat = self.env["res.partner.id_category"].search(
            [
                (
                    "partner_map_field",
                    "=",
                    AEAT_TYPES_ID_CATEGORY_MAP[aeat_identification_type],
                )
            ]
        )
        partners_via_id_number = (
            self.env["res.partner.id_number"]
            .search(
                [
                    ("name", "=", aeat_identification),
                    ("category_id", "in", identification_types_aeat.ids),
                ]
            )
            .mapped("partner_id")
            .filtered(lambda p: not p.parent_id)
        )
        if partners_via_id_number:
            return partners_via_id_number
        partners_via_aeat = self.search(
            [
                ("aeat_identification", "=", aeat_identification),
                ("aeat_identification_type", "=", aeat_identification_type),
                ("parent_id", "=", False),
            ]
        )
        if partners_via_aeat:
            return partners_via_aeat
        return None

    @api.constrains("aeat_identification", "aeat_identification_type", "parent_id")
    def _check_aeat_identification_unique(self):
        for record in self:
            if record.parent_id or not record.aeat_identification:
                continue
            identification_types_aeat = self.env["res.partner.id_category"].search(
                [
                    (
                        "partner_map_field",
                        "=",
                        AEAT_TYPES_ID_CATEGORY_MAP[record.aeat_identification_type],
                    )
                ]
            )
            if (
                self.env["res.partner.id_number"]
                .search(
                    [
                        ("name", "=", record.aeat_identification),
                        ("category_id", "in", identification_types_aeat.ids),
                        ("partner_id", "!=", record.id),
                    ]
                )
                .mapped("partner_id")
                .filtered(lambda p: not p.parent_id)
            ):
                raise ValidationError(
                    _(
                        "The AEAT Identification %s already exists in another "
                        "partner via identification number."
                    )
                    % record.aeat_identification
                )
            if self.env["res.partner"].search(
                [
                    ("aeat_identification", "=", record.aeat_identification),
                    ("aeat_identification_type", "=", record.aeat_identification_type),
                    ("id", "!=", record.id),
                    ("parent_id", "=", False),
                ]
            ):
                raise ValidationError(
                    _("The AEAT Identification %s already exists in another partner.")
                    % record.aeat_identification
                )
