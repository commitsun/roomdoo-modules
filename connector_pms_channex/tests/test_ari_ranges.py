# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import datetime

from odoo.tests import tagged
from odoo.tests.common import BaseCase

from ..models.common.ari import clip_ranges, merge_ranges


def _d(day):
    return datetime.date(2026, 1, 1) + datetime.timedelta(days=day)


@tagged("post_install", "-at_install")
class TestChannexAriRanges(BaseCase):
    """The nights pending, kept as ranges."""

    def test_ranges_overlapping_are_one(self):
        self.assertEqual(
            merge_ranges([(_d(5), _d(9)), (_d(1), _d(6))]), [(_d(1), _d(9))]
        )

    def test_a_range_inside_another_is_taken_in(self):
        self.assertEqual(
            merge_ranges([(_d(1), _d(9)), (_d(3), _d(4))]), [(_d(1), _d(9))]
        )

    def test_ranges_following_straight_on_are_one(self):
        self.assertEqual(
            merge_ranges([(_d(3), _d(4)), (_d(1), _d(2))]), [(_d(1), _d(4))]
        )

    def test_ranges_with_a_night_between_stay_apart(self):
        self.assertEqual(
            merge_ranges([(_d(4), _d(4)), (_d(1), _d(2))]),
            [(_d(1), _d(2)), (_d(4), _d(4))],
        )

    def test_open_ends_merge_without_overflowing(self):
        everything = (datetime.date.min, datetime.date.max)
        self.assertEqual(merge_ranges([(_d(1), _d(1)), everything]), [everything])
        self.assertEqual(
            merge_ranges([(datetime.date.min, _d(1)), (_d(2), datetime.date.max)]),
            [everything],
        )

    def test_clipping_cuts_to_the_window_and_drops_what_falls_out(self):
        self.assertEqual(
            clip_ranges(
                [
                    (datetime.date.min, _d(0)),
                    (_d(2), _d(3)),
                    (_d(8), datetime.date.max),
                ],
                _d(1),
                _d(10),
            ),
            [(_d(2), _d(3)), (_d(8), _d(10))],
        )
