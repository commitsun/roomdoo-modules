from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PmsRoomType(models.Model):
    _inherit = "pms.room.type"

    long_stay_period = fields.Selection(
        selection=[
            ("weekly", "Weekly"),
            ("monthly", "Monthly"),
        ],
        string="Long Stay Period",
        help="Defines the base duration of the long stay reservation.",
    )

    long_stay_price = fields.Monetary(
        string="Long Stay Base Price",
        help="Base price for the selected long stay period.",
    )

    long_stay_product_id = fields.Many2one(
        comodel_name="product.template",
        string="Long Stay Product",
        readonly=True,
        help="Internal product automatically created for long stay pricing.",
    )

    long_stay_tax_ids = fields.Many2many(
        comodel_name="account.tax",
        string="Long Stay Taxes",
        help="Taxes that will be assigned to the internal long stay product.",
    )

    @api.constrains("long_stay_period", "long_stay_price")
    def _check_long_stay_fields(self):
        """
        Ensure that both long_stay_period and long_stay_price
        are set together. Partial configuration is not allowed.
        """
        for room_type in self:
            if room_type.long_stay_period and not room_type.long_stay_price:
                raise ValidationError(
                    _(
                        "You must set a Long Stay Base Price when a Long Stay "
                        "Period is defined for room type '%s'."
                    )
                    % room_type.display_name
                )
            if room_type.long_stay_price and not room_type.long_stay_period:
                raise ValidationError(
                    _(
                        "You must set a Long Stay Period when a Long Stay "
                        "Base Price is defined for room type '%s'."
                    )
                    % room_type.display_name
                )

    def _get_long_stay_product_name(self):
        """
        Generate a default product name based on the room type
        and the selected long stay period.

        Example:
        "Double Room long stay monthly"
        """
        self.ensure_one()
        period_label = dict(self._fields["long_stay_period"].selection).get(
            self.long_stay_period, ""
        )
        return f"{self.display_name} long stay {period_label.lower()}"

    def _create_or_update_long_stay_product(self):
        """
        Automatically create or update the product.template used for
        long stay pricing.

        If the long stay configuration is incomplete, the product is
        deactivated. If both fields are set, the product is created
        or updated accordingly.
        """
        ProductTemplate = self.env["product.template"]

        for room_type in self:
            # If long stay configuration is incomplete, deactivate product
            if not room_type.long_stay_period or not room_type.long_stay_price:
                if room_type.long_stay_product_id:
                    room_type.long_stay_product_id.active = False
                continue

            # Build product values
            vals = {
                "name": room_type._get_long_stay_product_name(),
                "is_long_stay_product": True,
                "sale_ok": False,  # Not visible as a sellable service
                "list_price": room_type.long_stay_price,
                "type": "service",
                "active": True,
                "taxes_id": [(6, 0, room_type.long_stay_tax_ids.ids)],
                "categ_id": self.env.ref("pms.product_category_service").id,
            }

            # Update existing product
            if room_type.long_stay_product_id:
                room_type.long_stay_product_id.write(vals)

            # Create new product
            else:
                product = ProductTemplate.create(vals)
                room_type.long_stay_product_id = product.id

    @api.model
    def create(self, vals):
        """
        Extend create() to auto-generate long stay products
        if the long stay fields are set at creation.
        """
        room_types = super().create(vals)
        room_types._create_or_update_long_stay_product()
        return room_types

    def write(self, vals):
        """
        Extend write() to update or create the long stay product
        whenever the relevant configuration changes.
        """
        res = super().write(vals)

        tracked_fields = {"long_stay_period", "long_stay_price", "long_stay_tax_ids"}
        if tracked_fields.intersection(vals.keys()):
            self._create_or_update_long_stay_product()

        return res
