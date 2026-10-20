from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    channel_available = fields.Boolean(
        string="Sale Channel Available",
        help="If checked, the product will be available for Channel",
        default=False,
    )

    def _get_billed_day_price(
        self,
        pricelist,
        consumption_date,
        pms_property_id,
        adults=0,
        children=0,
        partner_id=False,
        fiscal_position=False,
        company=None,
    ):
        """Total amount that the services of this board service will bill on a
        given consumption date for the given adults and children.

        Mirrors ``pms.service._get_price_unit_line``: pricelist price on the
        consumption date, then ``_fix_tax_included_price_company`` with the
        fiscal-position-mapped taxes. External-API callers use it to split a
        package price (room + board) into the room portion without diverging
        from the price the board service lines will persist.
        """
        self.ensure_one()
        company = company or self.env.user.company_id
        account_tax = self.env["account.tax"]
        guests = {"adults": adults, "children": children, "all": adults + children}
        total = 0.0
        for product, guest_type in self._get_board_service_products(adults, children):
            qty = guests[guest_type] if product.per_person else 1
            raw_price = pricelist._get_product_price(
                product=product.with_context(
                    property=pms_property_id,
                    consumption_date=consumption_date,
                ),
                quantity=qty,
                partner=partner_id,
                consumption_date=consumption_date,
                pms_property_id=pms_property_id,
            )
            product_taxes = product.taxes_id.filtered(
                lambda r, c=company: not r.company_id or r.company_id == c,
            )
            line_taxes = (
                fiscal_position.map_tax(product_taxes)
                if fiscal_position
                else product_taxes
            )
            unit_price = account_tax._fix_tax_included_price_company(
                raw_price,
                product.taxes_id,
                line_taxes,
                company,
            )
            total += unit_price * qty
        return total
