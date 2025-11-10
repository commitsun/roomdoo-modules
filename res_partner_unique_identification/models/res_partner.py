from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import config


class ResPartner(models.Model):
    _inherit = "res.partner"

    complete_vat = fields.Char(compute="_compute_complete_vat", store=True)
    vat_without_country = fields.Char(
        compute="_compute_vat_without_country", store=True
    )

    @api.depends("vat")
    def _compute_vat_without_country(self):
        for record in self:
            if record.vat:
                vat_country, vat_number = self._split_vat(record.vat)
                # _split_vat can return first part of the vat  as country if
                # the vat does not have country code
                country = self.env["res.country"].search(
                    [("code", "=ilike", vat_country)], limit=1
                )
                if country:
                    record.vat_without_country = vat_number
                else:
                    record.vat_without_country = record.vat
            else:
                record.vat_without_country = False

    @api.depends("vat", "country_id")
    def _compute_complete_vat(self):
        for record in self:
            if record.vat:
                vat_country, vat_number = self._split_vat(record.vat)
                # _split_vat can return first part of the vat  as country if
                # the vat does not have country code
                country = self.env["res.country"].search(
                    [("code", "=ilike", vat_country)], limit=1
                )
                if country:
                    # The vat already includes the country code
                    record.complete_vat = vat_country.upper() + vat_number
                elif record.country_id and record.country_id.code:
                    # Prepend the country code
                    record.complete_vat = record.country_id.code + record.vat
                else:
                    record.complete_vat = record.vat
            else:
                record.complete_vat = False

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
            domain = [
                ("complete_vat", "=", record.complete_vat),
                ("id", "!=", record.id),
                "!",
                ("id", "child_of", record.id),
            ]
            if record.company_id:
                domain += [("company_id", "in", [False, record.company_id.id])]
            if self.search(domain, limit=1):
                raise ValidationError(
                    _("The VAT %s already exists in another partner.") % record.vat
                )
            identification_types_vat = self.env["res.partner.id_category"].search(
                [("aeat_identification_type", "in", ["02", "04"])]
            )
            if (
                self.env["res.partner.id_number"]
                .search(
                    [
                        ("name", "=", record.vat_without_country),
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

    @api.constrains("aeat_identification", "aeat_identification_type", "parent_id")
    def _check_aeat_identification_unique(self):
        for record in self:
            if record.parent_id or not record.aeat_identification:
                continue
            identification_types_aeat = self.env["res.partner.id_category"].search(
                [("aeat_identification_type", "=", record.aeat_identification_type)]
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
