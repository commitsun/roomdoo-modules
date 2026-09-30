from odoo import models


class AccountMove(models.Model):
    _inherit = "account.move"

    def _report_line_groups(self):
        """Arrange the invoice lines into the groups a report should print.

        Every product category decides whether its lines are printed one by
        one or merged into a single line carrying a per-product breakdown.
        Categories are left on the default 'detail' mode, so a report that
        calls this method without any category being configured gets exactly
        the invoice lines, in their original order.

        Returns a list of dicts, ordered by the category's report sequence and
        then by the position of the first line of the group in the invoice::

            {
                "category": product.category record (may be empty),
                "label": str,             # heading for the group
                "collapse": bool,         # True when merged into one line
                "lines": account.move.line recordset,
                "quantity": float,        # total quantity of the group
                "price_total": float,     # total with taxes
                "price_subtotal": float,  # total without taxes
                "detail": [               # only filled when collapse is True
                    {
                        "product": product.product record,
                        "name": str,
                        "quantity": float,
                        "price_total": float,
                    },
                ],
            }
        """
        self.ensure_one()
        groups = []
        groups_by_category = {}
        for position, line in enumerate(self._report_groupable_lines()):
            category = line.product_id.categ_id
            collapse = category.invoice_report_group_mode == "collapse"
            group = groups_by_category.get(category.id) if collapse else None
            if group is None:
                group = {
                    "category": category,
                    "label": (category.invoice_report_label or category.name or ""),
                    "collapse": collapse,
                    "lines": self.env["account.move.line"],
                    "quantity": 0.0,
                    "price_total": 0.0,
                    "price_subtotal": 0.0,
                    "detail": [],
                    # not part of the public contract: sorting only
                    "_sequence": category.invoice_report_sequence,
                    "_position": position,
                }
                groups.append(group)
                if collapse:
                    groups_by_category[category.id] = group
            group["lines"] |= line
            group["quantity"] += line.quantity
            group["price_total"] += line.price_total
            group["price_subtotal"] += line.price_subtotal

        for group in groups:
            if group["collapse"]:
                group["detail"] = self._report_group_detail(group["lines"])
        groups.sort(key=lambda group: (group["_sequence"], group["_position"]))
        for group in groups:
            del group["_sequence"]
            del group["_position"]
        return groups

    def _report_groupable_lines(self):
        """The invoice lines a report groups: the real product lines.

        Sections and notes are laid out by the report itself, and rounding
        lines carry no product, so neither takes part in the grouping.
        """
        self.ensure_one()
        return self.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )

    def _report_group_detail(self, lines):
        """Break a collapsed group down into one entry per product.

        Several invoice lines for the same product -- one per cup of coffee,
        say -- are added up so the customer reads "4 x Coffee  5.20" instead
        of four identical lines.
        """
        self.ensure_one()
        detail_by_product = {}
        for line in lines:
            entry = detail_by_product.get(line.product_id.id)
            if entry is None:
                entry = {
                    "product": line.product_id,
                    "name": line.product_id.name or line.name or "",
                    "quantity": 0.0,
                    "price_total": 0.0,
                }
                detail_by_product[line.product_id.id] = entry
            entry["quantity"] += line.quantity
            entry["price_total"] += line.price_total
        return sorted(
            detail_by_product.values(),
            key=lambda entry: (-entry["price_total"], entry["name"]),
        )
