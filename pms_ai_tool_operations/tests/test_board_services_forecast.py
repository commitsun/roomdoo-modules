from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged

from .common import SETUP_DAY, OperationsToolsMixin

TOOL = "pms_board_services_forecast"


@tagged("post_install", "-at_install")
class TestBoardServicesForecast(OperationsToolsMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_hotels()
        Product = cls.env["product.product"]
        cls.breakfast = Product.create(
            {
                "name": "AI ops breakfast",
                "type": "service",
                "per_day": True,
                "consumed_on": "after",
            }
        )
        cls.dinner = Product.create(
            {
                "name": "AI ops dinner",
                "type": "service",
                "per_day": True,
                "consumed_on": "before",
            }
        )
        cls.extra = Product.create({"name": "AI ops extra", "type": "service"})
        # The lines are given as (day, units): the date of a line is the day the
        # product is consumed.
        main = cls._reservation(1, 3)
        cls._board(main, cls.breakfast, [(2, 2), (3, 2)])
        cls._board(main, cls.dinner, [(1, 2), (2, 2)])
        cls._board(main, cls.extra, [(2, 7)], board=False)  # not a board service
        second = cls._reservation(2, 3)
        cls._board(second, cls.breakfast, [(3, 3)])
        cls._board(second, cls.dinner, [(2, 3)])
        staff = cls._reservation(1, 2, rtype="staff")
        cls._board(staff, cls.breakfast, [(2, 1)])
        cancelled = cls._reservation(1, 3)
        cls._board(cancelled, cls.breakfast, [(2, 5)])
        cancelled.action_cancel()
        pre_reservation = cls._reservation(1, 3, state="draft")
        cls._board(pre_reservation, cls.breakfast, [(2, 4)])
        other = cls._reservation(1, 3, prop=cls.other_prop)
        cls._board(other, cls.breakfast, [(2, 6)])

    @classmethod
    def _board(cls, reservation, product, lines, board=True):
        with freeze_time(SETUP_DAY):
            return cls.env["pms.service"].create(
                {
                    "reservation_id": reservation.id,
                    "product_id": product.id,
                    "is_board_service": board,
                    "no_auto_add_lines": True,
                    "service_line_ids": [
                        (0, 0, {"date": cls.day(day), "day_qty": units})
                        for day, units in lines
                    ],
                }
            )

    def _quantities(self, row):
        return {
            day["date"]: {p["product"]: p["quantity"] for p in day["products"]}
            for day in row["days"]
        }

    def test_units_per_property_and_day(self):
        response = self._call(
            TOOL, date_from=str(self.day(1)), date_to=str(self.day(3))
        )
        (row,) = response["result"]["by_property"]
        self.assertEqual(row["property"], "AI ops hotel")
        self.assertEqual(
            self._quantities(row),
            {
                str(self.day(1)): {"AI ops dinner": 2},
                str(self.day(2)): {"AI ops breakfast": 3, "AI ops dinner": 5},
                str(self.day(3)): {"AI ops breakfast": 5},
            },
        )
        self.assertEqual(
            [(p["product"], p["quantity"]) for p in response["result"]["totals"]],
            [("AI ops breakfast", 8), ("AI ops dinner", 7)],
        )
        self.assertEqual(response["summary"], "15 units of 2 board service products.")
        self.assertEqual(
            response["period"],
            {"date_from": str(self.day(1)), "date_to": str(self.day(3))},
        )

    def test_cancelled_pre_reservations_and_extras_are_left_out(self):
        response = self._call(
            TOOL, date_from=str(self.day(2)), date_to=str(self.day(2))
        )
        (row,) = response["result"]["by_property"]
        # 2 (main) + 1 (staff); the cancelled 5, the pre-reservation 4 and the
        # extra service 7 are not there.
        self.assertEqual(
            self._quantities(row)[str(self.day(2))],
            {"AI ops breakfast": 3, "AI ops dinner": 5},
        )
        self.assertIn("not included: 4.", " ".join(response["warnings"]))

    def test_defaults_to_tomorrow(self):
        response = self._call(TOOL)
        tomorrow = str(self.day(1))
        self.assertEqual(
            response["period"], {"date_from": tomorrow, "date_to": tomorrow}
        )
        (row,) = response["result"]["by_property"]
        self.assertEqual(self._quantities(row), {tomorrow: {"AI ops dinner": 2}})
        # Without date_to it is that one day.
        one_day = self._call(TOOL, date_from=str(self.day(3)))
        self.assertEqual(
            one_day["period"],
            {"date_from": str(self.day(3)), "date_to": str(self.day(3))},
        )
        self.assertEqual(
            self._quantities(one_day["result"]["by_property"][0]),
            {str(self.day(3)): {"AI ops breakfast": 5}},
        )

    def test_scope_of_the_user(self):
        period = {"date_from": str(self.day(1)), "date_to": str(self.day(3))}
        response = self._call(TOOL, **period)
        self.assertEqual(
            [row["property_id"] for row in response["result"]["by_property"]],
            self.prop.ids,
        )
        with self.assertRaises(AccessError):
            self._call(TOOL, property_ids=self.other_prop.ids, **period)
        both = self._call(TOOL, user=self.user_all, **period)
        rows = {row["property"]: row for row in both["result"]["by_property"]}
        self.assertEqual(
            self._quantities(rows["AI ops other hotel"]),
            {str(self.day(2)): {"AI ops breakfast": 6}},
        )
        self.assertEqual(
            [(p["product"], p["quantity"]) for p in both["result"]["totals"]],
            [("AI ops breakfast", 14), ("AI ops dinner", 7)],
        )

    def test_properties_without_board_services_are_named(self):
        response = self._call(
            TOOL,
            user=self.user_all,
            date_from=str(self.day(1)),
            date_to=str(self.day(1)),
        )
        self.assertEqual(
            [row["property"] for row in response["result"]["by_property"]],
            ["AI ops hotel"],
        )
        self.assertIn(
            "No board service in the period for: AI ops other hotel.",
            response["warnings"],
        )

    def test_board_service_of_the_room_type(self):
        breakfast = self.env["product.product"].create(
            {
                "name": "AI ops real breakfast",
                "type": "service",
                "per_day": True,
                "per_person": True,
                "consumed_on": "after",
            }
        )
        board = self.env["pms.board.service"].create(
            {
                "name": "AI ops bed and breakfast",
                "default_code": "AIBB",
                "pms_property_ids": [(6, 0, self.prop.ids)],
            }
        )
        self.env["pms.board.service.line"].create(
            {
                "pms_board_service_id": board.id,
                "product_id": breakfast.id,
                "amount": 5,
                "adults": True,
            }
        )
        room_board = self.env["pms.board.service.room.type"].create(
            {
                "pms_room_type_id": self.room_type.id,
                "pms_board_service_id": board.id,
                "pms_property_id": self.prop.id,
            }
        )
        self._reservation(5, 7, adults=2, board_service_room_id=room_board.id)
        response = self._call(
            TOOL, date_from=str(self.day(5)), date_to=str(self.day(8))
        )
        (row,) = response["result"]["by_property"]
        # Breakfast is consumed after each night: the days 6 and 7, for 2 adults.
        self.assertEqual(
            self._quantities(row),
            {
                str(self.day(6)): {"AI ops real breakfast": 2},
                str(self.day(7)): {"AI ops real breakfast": 2},
            },
        )

    def test_envelope(self):
        response = self._call(
            TOOL, date_from=str(self.day(1)), date_to=str(self.day(3))
        )
        self.assertEqual(response["environment"], "test")
        self.assertEqual(response["tool"], TOOL)
        self.assertEqual(len(response["definitions"]), 2)
        self.assertTrue(response["generated_at"])

    def test_period_and_arguments_are_validated(self):
        bad_calls = (
            {"date_from": str(self.day(3)), "date_to": str(self.day(1))},
            {"date_from": str(self.day(1)), "date_to": str(self.day(15))},
            {"date_from": "22/09/2026"},
            {"date_to": 20260922},
            {"domain": "[]"},
            {"property_ids": ["1"]},
        )
        for kwargs in bad_calls:
            with self.subTest(kwargs=kwargs), self.assertRaises(UserError):
                self._call(TOOL, **kwargs)
        # 14 days in all is the limit.
        response = self._call(
            TOOL, date_from=str(self.day(1)), date_to=str(self.day(14))
        )
        self.assertEqual(response["period"]["date_to"], str(self.day(14)))
