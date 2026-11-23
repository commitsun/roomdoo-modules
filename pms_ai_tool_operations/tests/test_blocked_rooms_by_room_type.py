from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged

from .common import TODAY, OperationsToolsMixin

TOOL = "pms_blocked_rooms_by_room_type"


@tagged("post_install", "-at_install")
class TestBlockedRoomsByRoomType(OperationsToolsMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_hotels()
        # Hotel: two blocks today, one of them ends today (last night is day 1).
        cls._reservation(0, 2, rtype="out")
        cls._reservation(0, 1, rtype="out")
        cls._reservation(1, 3, rtype="out")
        cls._reservation(0, 1)  # a guest: not a block
        cls._reservation(0, 1, rtype="out", state="cancel")  # cancelled block
        # Other hotel: one block today.
        cls._reservation(0, 1, rtype="out", prop=cls.other_prop)

    def _by_property(self, response):
        return {row["property_id"]: row for row in response["result"]["by_property"]}

    def test_counts_per_property_and_total_by_code(self):
        response = self._call(
            TOOL, user=self.user_all, room_type_code="aiod", date=str(TODAY)
        )
        rows = self._by_property(response)
        self.assertEqual(rows[self.prop.id]["blocked_rooms"], 2)
        self.assertEqual(rows[self.prop.id]["rooms_of_type"], 5)
        self.assertEqual(rows[self.other_prop.id]["blocked_rooms"], 1)
        self.assertEqual(
            response["result"]["totals"],
            {
                "properties_with_the_type": 2,
                "properties_with_blocks": 2,
                "rooms_of_type": 7,
                "blocked_rooms": 3,
            },
        )
        self.assertEqual(
            response["period"], {"date_from": str(TODAY), "date_to": str(TODAY)}
        )
        self.assertEqual(
            response["result"]["by_property"][0]["property_id"], self.prop.id
        )

    def test_date_defaults_to_today_and_filters_by_id(self):
        response = self._call(TOOL, room_type_id=self.room_type.id)
        (row,) = response["result"]["by_property"]
        self.assertEqual(row["blocked_rooms"], 2)
        later = self._call(TOOL, room_type_id=self.room_type.id, date=str(self.day(1)))
        self.assertEqual(later["result"]["totals"]["blocked_rooms"], 2)
        after = self._call(TOOL, room_type_id=self.room_type.id, date=str(self.day(2)))
        self.assertEqual(after["result"]["totals"]["blocked_rooms"], 1)
        free = self._call(TOOL, room_type_id=self.room_type.id, date=str(self.day(9)))
        self.assertEqual(free["result"]["totals"]["blocked_rooms"], 0)
        self.assertEqual(free["result"]["totals"]["properties_with_blocks"], 0)

    def test_other_room_type_has_no_blocks(self):
        response = self._call(TOOL, room_type_code="AIOH")
        self.assertEqual(response["result"]["totals"]["blocked_rooms"], 0)
        unknown = self._call(TOOL, room_type_code="NOPE")
        self.assertEqual(unknown["result"]["by_property"], [])
        self.assertIn("No room type matches", " ".join(unknown["warnings"]))

    def test_scope_of_the_user(self):
        response = self._call(TOOL, room_type_code="AIOD")
        self.assertEqual(list(self._by_property(response)), self.prop.ids)
        with self.assertRaises(AccessError):
            self._call(TOOL, room_type_code="AIOD", property_ids=self.other_prop.ids)

    def test_arguments_are_validated(self):
        for kwargs in (
            {"date": "21/09/2026"},
            {"room_type_id": "1"},
            {"room_type_code": 5},
            {"domain": "[]"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(UserError):
                self._call(TOOL, **kwargs)
