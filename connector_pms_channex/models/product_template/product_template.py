# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models

#: What Channex can be told a rate plan includes to eat. Its ``none``, which is
#: telling nothing, is the field left empty. ``carribean`` is spelt as Channex
#: spells it.
MEAL_TYPES = [
    ("room_only", "Room only"),
    ("breakfast", "Breakfast"),
    ("bed_and_breakfast", "Bed and breakfast"),
    ("buffet_breakfast", "Buffet breakfast"),
    ("continental_breakfast", "Continental breakfast"),
    ("english_breakfast", "English breakfast"),
    ("full_breakfast", "Full breakfast"),
    ("carribean_breakfast", "Caribbean breakfast"),
    ("lunch", "Lunch"),
    ("dinner", "Dinner"),
    ("breakfast_and_lunch", "Breakfast and lunch"),
    ("lunch_and_dinner", "Lunch and dinner"),
    ("half_board", "Half board"),
    ("dinner_bed_and_breakfast_plan", "Dinner, bed and breakfast"),
    ("full_board", "Full board"),
    ("all_inclusive", "All inclusive"),
    ("self_catering", "Self catering"),
    ("american", "American plan"),
    ("family_american", "Family American plan"),
    ("bermuda", "Bermuda plan"),
    ("european_plan", "European plan"),
    ("family_plan", "Family plan"),
]


class ProductTemplate(models.Model):
    _inherit = "product.template"

    channex_meal_type = fields.Selection(
        selection=MEAL_TYPES,
        string="Meal plan on Channex",
        help="What Channex, and the channels it sells through, are told this "
        "board service includes. Left empty, they are told nothing.",
    )
