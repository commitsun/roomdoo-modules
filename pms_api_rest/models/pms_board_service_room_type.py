from odoo import models


class PmsBoardServiceRoomType(models.Model):
    _inherit = "pms.board.service.room.type"

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
        """Total amount that the system will bill for this board service
        on a given consumption date with the given (adults, children) mix.

        Mirrors ``pms.service.line._get_price_unit_line``: pricelist with
        ``board_service_line_id`` + ``consumption_date`` context, then
        ``_fix_tax_included_price_company`` with the fiscal-position-mapped
        taxes. External-API callers can use this to split a package price
        (room + board) into the room portion without diverging from the
        price the auto-computed service line will ultimately persist.
        """
        self.ensure_one()
        company = company or self.env.user.company_id
        account_tax = self.env["account.tax"]
        total = 0.0
        for bsl in self.board_service_line_ids.with_context(
            property=pms_property_id,
        ):
            if bsl.adults and adults:
                qty = adults
            elif bsl.children and children:
                qty = children
            else:
                continue
            raw_price = pricelist._get_product_price(
                product=bsl.product_id.with_context(
                    board_service_line_id=bsl.id,
                    property=pms_property_id,
                    consumption_date=consumption_date,
                ),
                quantity=qty,
                partner=partner_id,
                consumption_date=consumption_date,
                pms_property_id=pms_property_id,
                board_service_line_id=bsl.id,
            )
            product_taxes = bsl.product_id.taxes_id.filtered(
                lambda r, c=company: not r.company_id or r.company_id == c,
            )
            line_taxes = (
                fiscal_position.map_tax(product_taxes)
                if fiscal_position
                else product_taxes
            )
            unit_price = account_tax._fix_tax_included_price_company(
                raw_price,
                bsl.product_id.taxes_id,
                line_taxes,
                company,
            )
            total += unit_price * qty
        return total
