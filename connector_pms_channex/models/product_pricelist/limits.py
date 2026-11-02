# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""What a pricelist sold through Channex cannot have, because Channex has no
way to say it.

Each check is made wherever what it checks can change, and refused there. The
errors explain why at length: whoever meets one is editing a price or a
restriction, and need not know anything about Channex.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import format_date

from ..common.board import child_products
from .product_pricelist import BINDING_MODEL

#: How many of the rules in the way an error lists.
LISTED = 10


class ProductPricelist(models.Model):
    _inherit = "product.pricelist"

    def _channex_sold_in(self):
        """:return: ``(pricelist, property)`` for every property these
        pricelists are sold in through Channex."""
        sold = []
        # As superuser: anyone editing a price gets here, and the bindings are
        # for connector managers to see.
        for binding in (
            self.env[BINDING_MODEL].sudo().search([("odoo_id", "in", self.ids)])
        ):
            pricelist = binding.odoo_id
            pms_property = binding.backend_id.pms_property_id
            if (
                not pricelist.pms_property_ids
                or pms_property in pricelist.pms_property_ids
            ):
                sold.append((pricelist, pms_property))
        return sold

    @api.constrains("board_service_ids", "pms_property_ids")
    def _check_channex_children_board(self):
        self._channex_check_children_board()

    @api.constrains("availability_plan_id")
    def _check_channex_max_stay_arrival(self):
        self._channex_check_max_stay_arrival()

    def _channex_check_children_board(self):
        """Refuse what a child consumes of a board service priced by date in a
        pricelist sold through Channex.

        Channex charges every child the children fee of the rate plan, one
        amount that is the same every night. A price that changes from one
        night to another has no way to get there.
        """
        for pricelist, pms_property in self._channex_sold_in():
            problems = []
            for board_service in pricelist._get_available_board_services(
                pms_property, None, None
            ):
                for product in child_products(board_service):
                    item = pricelist._channex_dated_item(product, pms_property)
                    if item:
                        problems.append(
                            _(
                                '- %(product)s: priced by the rule "%(rule)s" of '
                                '"%(pricelist)s", which has dates (%(dates)s).'
                            )
                            % {
                                "product": product.display_name,
                                "rule": item.name,
                                "pricelist": item.pricelist_id.display_name,
                                "dates": item._channex_dates(),
                            }
                        )
            if problems:
                raise ValidationError(
                    _(
                        "In a pricelist sold through Channex, what a child "
                        "consumes of a board service cannot have a price that "
                        "changes with the date: Channex charges each child one "
                        "fixed amount, the same every night.\n\n"
                        'In "%(pricelist)s" these products do:\n%(problems)s\n\n'
                        "Give each of them a price without dates in "
                        '"%(pricelist)s". If adults and children pay the same '
                        "and you want prices by date, add the attribute "
                        '"%(attribute)s" to the board service: adults and '
                        "children then get a variant each, and only the "
                        "children's one needs a fixed price."
                    )
                    % {
                        "pricelist": pricelist.display_name,
                        "problems": "\n".join(problems),
                        "attribute": self.env.ref(
                            "pms.product_attribute_board_guest"
                        ).name,
                    }
                )

    def _channex_dated_item(self, product, pms_property):
        """The item that makes the price of a product in this pricelist change
        with the date, if one does: the first one pms applies to the product,
        when it does not apply on every date, or the one doing that wherever it
        takes its price from."""
        self.ensure_one()
        # As pms looks for them: archived items too, and in the order it tries
        # them, the first one that applies being the one that prices.
        items = (
            self.env["product.pricelist.item"]
            .with_context(active_test=False)
            .search(
                [
                    ("pricelist_id", "=", self.id),
                    "|",
                    ("categ_id", "=", False),
                    ("categ_id", "parent_of", product.categ_id.ids),
                    "|",
                    ("product_tmpl_id", "=", False),
                    ("product_tmpl_id", "=", product.product_tmpl_id.id),
                    "|",
                    ("product_id", "=", False),
                    ("product_id", "=", product.id),
                    "|",
                    ("pms_property_ids", "=", False),
                    ("pms_property_ids", "in", pms_property.ids),
                ]
            )
        )
        for item in items:
            # One that is over prices nothing Channex still sells.
            if not item._is_applicable_for(product, 1) or item._channex_is_over():
                continue
            if item._channex_dates():
                return item
            if (
                item.compute_price != "fixed"
                and item.base == "pricelist"
                and item.base_pricelist_id
            ):
                return item.base_pricelist_id._channex_dated_item(product, pms_property)
            break
        return self.env["product.pricelist.item"]

    def _channex_check_max_stay_arrival(self):
        """Refuse a max stay on arrival under a pricelist sold through
        Channex, which has a single max stay that the channels apply to the
        whole stay.

        Only the rules that still apply count: one that ended before today is
        not published again.
        """
        for pricelist, pms_property in self._channex_sold_in():
            plans = pricelist.availability_plan_id._channex_ancestors()
            if not plans:
                continue
            rules = (
                self.env["pms.availability.plan.rule"]
                .sudo()
                .search(
                    [
                        ("availability_plan_id", "in", plans.ids),
                        ("pms_property_id", "=", pms_property.id),
                        ("max_stay_arrival", "!=", 0),
                        ("date_to", ">=", fields.Date.context_today(self)),
                    ],
                    order="availability_plan_id, room_type_id, date_from",
                )
            )
            if not rules:
                continue
            listed = [
                _("- %(plan)s, %(room_type)s, %(date_from)s to %(date_to)s: %(value)s")
                % {
                    "plan": rule.availability_plan_id.display_name,
                    "room_type": rule.room_type_id.display_name,
                    "date_from": format_date(self.env, rule.date_from),
                    "date_to": format_date(self.env, rule.date_to),
                    "value": rule.max_stay_arrival,
                }
                for rule in rules[:LISTED]
            ]
            if len(rules) > LISTED:
                listed.append(_("... and %s more.") % (len(rules) - LISTED))
            rule_fields = rules._fields
            raise ValidationError(
                _(
                    'A pricelist sold through Channex cannot have "%(arrival)s" '
                    "in its availability plan: Channex has a single maximum "
                    "stay, which the channels apply to the whole stay, and "
                    "nothing like a maximum stay by day of arrival.\n\n"
                    '"%(pricelist)s" has it, from today on, in these rules:\n'
                    "%(rules)s\n\n"
                    'Clear "%(arrival)s" in them. "%(max_stay)s", which Channex '
                    "does have, can take its place."
                )
                % {
                    "arrival": rule_fields["max_stay_arrival"]._description_string(
                        self.env
                    ),
                    "max_stay": rule_fields["max_stay"]._description_string(self.env),
                    "pricelist": pricelist.display_name,
                    "rules": "\n".join(listed),
                }
            )
