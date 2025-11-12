from odoo import _, api, models
from odoo.exceptions import ValidationError
from odoo.osv import expression
from odoo.tools import config


class ResPartner(models.Model):
    _inherit = "res.partner"

    def _get_vat_with_country(self, vat, country=None):
        vat_country, vat_number = self._split_vat(vat)
        # _split_vat can return first part of the vat  as country if
        # the vat does not have country code
        country_in_vat = self.env["res.country"].search(
            [("code", "=ilike", vat_country)], limit=1
        )
        if country_in_vat:
            return vat
        elif country and country.code:
            return country.code + vat
        else:
            return vat

    def _get_vat_without_country(self, vat, country=None):
        vat_country, vat_number = self._split_vat(vat)
        # _split_vat can return first part of the vat  as country if
        # the vat does not have country code
        country_in_vat = self.env["res.country"].search(
            [("code", "=ilike", vat_country)], limit=1
        )
        if country_in_vat:
            return vat_number
        else:
            return vat

    def _get_vat_country_code(self, vat, country=None):
        vat_country, vat_number = self._split_vat(vat)
        # _split_vat can return first part of the vat  as country if
        # the vat does not have country code
        country_in_vat = self.env["res.country"].search(
            [("code", "=ilike", vat_country)], limit=1
        )
        if country_in_vat:
            return country_in_vat.id
        else:
            return country.id if country else False

    @api.model
    def get_duplicate_vat(self, vat, country):
        vat_with_country = self._get_vat_with_country(vat, country)
        vat_without_country = self._get_vat_without_country(vat, country)
        vat_domain = expression.OR(
            [
                [("vat", "=ilike", vat_with_country)],
                [
                    ("vat", "=ilike", vat_without_country),
                    ("country_id", "=", country.id),
                ],
            ]
        )
        duplicate_vat = self.search(vat_domain, limit=1)
        if duplicate_vat:
            return duplicate_vat
        identification_types_vat = self.env["res.partner.id_category"].search(
            [("partner_map_field", "=", "vat")]
        )
        duplicate_id_number = (
            self.env["res.partner.id_number"]
            .search(
                [
                    ("name", "=ilike", vat_without_country),
                    (
                        "country_id",
                        "=",
                        self._get_vat_country_code(vat, country),
                    ),
                    ("category_id", "in", identification_types_vat.ids),
                ]
            )
            .mapped("partner_id")
            .filtered(lambda p: not p.parent_id)
        )
        if duplicate_id_number:
            return duplicate_id_number[0]
        return None

    @api.constrains("vat", "parent_id")
    def _check_vat_unique(self):
        for record in self:
            if record.parent_id or not record.vat:
                continue
            test_condition = config["test_enable"] and not self.env.context.get(
                "test_vat"
            )
            if test_condition:
                continue
            vat_with_country = record._get_vat_with_country(
                record.vat, record.country_id
            )
            vat_without_country = record._get_vat_without_country(
                record.vat, record.country_id
            )
            vat_domain = expression.OR(
                [
                    [("vat", "=ilike", vat_with_country)],
                    [
                        ("vat", "=ilike", vat_without_country),
                        ("country_id", "=", record.country_id.id),
                    ],
                ]
            )
            domain = expression.AND(
                [
                    [
                        ("id", "!=", record.id),
                        "!",
                        ("id", "child_of", record.id),
                    ],
                    vat_domain,
                ]
            )
            if record.company_id:
                domain = expression.AND(
                    [domain, [("company_id", "in", [False, record.company_id.id])]]
                )
            if self.search(domain, limit=1):
                raise ValidationError(
                    _("The VAT %s already exists in another partner.") % record.vat
                )
            identification_types_vat = self.env["res.partner.id_category"].search(
                [("partner_map_field", "=", "vat")]
            )
            if (
                self.env["res.partner.id_number"]
                .search(
                    [
                        ("name", "=", vat_without_country),
                        (
                            "country_id",
                            "=",
                            self._get_vat_country_code(record.vat, record.country_id),
                        ),
                        ("category_id", "in", identification_types_vat.ids),
                        ("partner_id", "!=", record.id),
                    ]
                )
                .mapped("partner_id")
                .filtered(lambda p: not p.parent_id)
            ):
                raise ValidationError(
                    _(
                        "The VAT %s already exists in another partner via "
                        "identification number."
                    )
                    % record.vat
                )
