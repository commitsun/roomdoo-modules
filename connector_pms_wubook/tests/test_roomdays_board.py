# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.tests import BaseCase, tagged

from ..models.pms_folio.adapter import roomdays_board


def _room(*codes):
    return {"roomdays": [{"board_code": code} for code in codes]}


@tagged("post_install", "-at_install")
class TestRoomdaysBoard(BaseCase):
    """The board of a room whose channel sends it night by night."""

    def test_the_same_board_every_night(self):
        self.assertEqual(roomdays_board(_room("bb", "bb", "bb")), "bb")

    def test_nights_with_different_boards_give_none(self):
        self.assertIsNone(roomdays_board(_room("bb", "hb")))

    def test_nights_without_a_board_are_ignored(self):
        self.assertEqual(roomdays_board(_room("bb", None, "bb")), "bb")

    def test_no_board_code_is_no_board(self):
        self.assertIsNone(roomdays_board(_room("nb", "nb")))

    def test_a_room_without_nights(self):
        self.assertIsNone(roomdays_board({}))
        self.assertIsNone(roomdays_board({"roomdays": None}))
