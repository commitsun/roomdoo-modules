from odoo import _, api, models
from odoo.exceptions import ValidationError


class ResPartnerIdNumber(models.Model):
    _inherit = "res.partner.id_number"

    @api.constrains("name", "category_id", "partner_id")
    def _check_id_number_unique(self):
        for record in self:
            if not record.name:
                continue
            if self.search(
                [
                    ("name", "=", record.name),
                    ("country_id", "=", record.country_id.id),
                    ("category_id", "=", record.category_id.id),
                    ("partner_id", "!=", record.partner_id.id),
                ]
            ):
                raise ValidationError(
                    _("The identification number %s already exists in another partner.")
                    % record.name
                )
            if record.category_id.aeat_identification_type in ["02", "04"]:
                if self.env["res.partner"].search(
                    [
                        ("vat_without_country", "=", record.name),
                        ("id", "!=", record.partner_id.id),
                        ("parent_id", "=", False),
                    ]
                ):
                    raise ValidationError(
                        _("The VAT %s already exists in another partner.") % record.name
                    )
            else:
                if self.env["res.partner"].search(
                    [
                        ("aeat_identification", "=", record.name),
                        (
                            "aeat_identification_type",
                            "=",
                            record.category_id.aeat_identification_type,
                        ),
                        ("id", "!=", record.partner_id.id),
                        ("parent_id", "=", False),
                    ]
                ):
                    raise ValidationError(
                        _(
                            "The AEAT Identification %s already exists in"
                            " another partner."
                        )
                        % record.name
                    )
