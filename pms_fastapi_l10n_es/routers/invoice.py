from odoo import models

from odoo.addons.l10n_es_aeat_partner_identification.models.res_partner import (
    AEAT_TYPES_ID_CATEGORY_MAP,
)

_PASSPORT_AEAT_TYPE = "03"


class PmsApiInvoiceRouterHelper(models.AbstractModel):
    _inherit = "pms_api_invoice.invoice_router.helper"

    def _check_fiscal_id(self, partner, pms_property) -> list[dict]:
        """Spanish fiscal identification check.

        Valid if the contact has a VAT number or both aeat_identification_type
        and aeat_identification set. Additionally, passport (type 03) cannot
        be used to invoice when the document was issued by Spain.
        """
        has_fiscal_id = bool(partner.vat) or bool(
            partner.aeat_identification_type and partner.aeat_identification
        )
        errors = []
        if not has_fiscal_id:
            errors.append(
                {
                    "type": "/errors/missing-fiscal-id",
                    "title": "Missing fiscal identification number",
                    "detail": (
                        "El contacto no tiene número de identificación"
                        " fiscal configurado."
                    ),
                }
            )
        if (
            partner.aeat_identification_type == _PASSPORT_AEAT_TYPE
            and self._is_spanish_passport(partner)
        ):
            errors.append(
                {
                    "type": "/errors/invalid-id-type-for-country",
                    "title": "Invalid identification type for country",
                    "detail": (
                        "No se puede emitir factura a un ciudadano español"
                        " con número de pasaporte."
                    ),
                }
            )
        return errors

    def _is_spanish_passport(self, partner) -> bool:
        """Return True if the partner's passport document was issued by Spain."""
        passport_categories = self.env["res.partner.id_category"].search(
            [
                (
                    "partner_map_field",
                    "=",
                    AEAT_TYPES_ID_CATEGORY_MAP[_PASSPORT_AEAT_TYPE],
                )
            ]
        )
        passport_id_number = partner.id_numbers.filtered(
            lambda n: n.category_id in passport_categories
        )
        spain = self.env.ref("base.es")
        return bool(passport_id_number and passport_id_number[:1].country_id == spain)
