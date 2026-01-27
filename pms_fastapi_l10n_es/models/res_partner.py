from odoo import models

from odoo.addons.l10n_es_aeat_partner_identification.models.res_partner import (
    AEAT_TYPES_ID_CATEGORY_MAP,
)


class ResPartner(models.Model):
    _inherit = "res.partner"

    def set_fiscal_document_data(
        self, fiscal_id_number=False, fiscal_id_number_type=False
    ):
        if not fiscal_id_number and not fiscal_id_number_type:
            return
        if not fiscal_id_number:
            if self.vat:
                fiscal_id_number = self.vat
            else:
                fiscal_id_number = self.aeat_identification
        if not fiscal_id_number_type and self.vat:
            fiscal_id_number_type = "vat"
        elif not fiscal_id_number_type and self.aeat_identification:
            fiscal_id_number_type = AEAT_TYPES_ID_CATEGORY_MAP[
                self.aeat_identification_type
            ]
        if fiscal_id_number_type not in AEAT_TYPES_ID_CATEGORY_MAP.values():
            self.write(
                {
                    "aeat_identification_type": False,
                    "aeat_identification": False,
                }
            )
            return super().set_fiscal_document_data(
                fiscal_id_number, fiscal_id_number_type
            )
        odoo_fiscal_type = [
            key
            for key, value in AEAT_TYPES_ID_CATEGORY_MAP.items()
            if value == fiscal_id_number_type
        ]
        self.write(
            {
                "vat": False,
                "aeat_identification_type": odoo_fiscal_type[0],
                "aeat_identification": fiscal_id_number,
            }
        )
