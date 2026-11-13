# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""What a board service costs on a night.

A rate plan is published at the room plus its board service, and a booking
comes back at that same sum, which pms keeps apart again: the board service on
lines of its own, the room on the rest. Taking the board service off has to
come to exactly what was added, so both are worked out here and nowhere else.

Children are charged for a board service as pms charges them. Should that
change, this module is the one place it changes.
"""


def board_unit(pricelist, product, pms_property, night):
    """What one guest pays for one product of a board service, on a night.

    Priced on the night it goes with, also what is consumed the morning after
    it: the night is what Channex sells, and what its price was published for.
    """
    return pricelist._get_product_price(
        product,
        1,
        consumption_date=night,
        pms_property_id=pms_property.id,
    )


def board_units(pricelist, board_service, pms_property, night):
    """What each product of a board service costs on a night, whoever it is
    for.

    :return: dict of product.product -> unit price
    """
    # One guest of each kind, so that every product is in.
    products = board_service._get_board_service_products(1, 1)
    return {
        product: board_unit(pricelist, product, pms_property, night)
        for product, _guest_type in products
    }


def board_total(units, adults, children):
    """What the board service comes to for the guests of a room, as pms
    charges it: once per guest it is for when charged per person, once
    otherwise, and not at all when the room has nobody it is for.

    :param units: as ``board_units`` returns them
    """
    guests = {"adults": adults, "children": children, "all": adults + children}
    total = 0.0
    for product, unit in units.items():
        count = guests[product.board_guest_type]
        if count:
            total += unit * (count if product.per_person else 1)
    return total
