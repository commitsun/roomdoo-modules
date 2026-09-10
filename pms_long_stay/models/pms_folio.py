from odoo import fields, models


class PmsFolio(models.Model):
    _inherit = "pms.folio"

    reservation_type = fields.Selection(
        selection_add=[("long_stay", "Long Stay")],
    )

    # ---------------------------------------------------------
    # EXTEND SERVICE PRICING TYPES
    # ---------------------------------------------------------
    def _get_reservation_types_with_service_pricing(self):
        """
        Extend base service pricing types to include 'long_stay' so that
        long stay reservations also use standard service pricing logic.
        """
        types = list(super()._get_reservation_types_with_service_pricing())
        if "long_stay" not in types:
            types.append("long_stay")
        return tuple(types)
