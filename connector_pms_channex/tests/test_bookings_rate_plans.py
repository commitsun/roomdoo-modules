# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import datetime

from odoo.tests import tagged

from .test_ari_restrictions import ChannexRestrictionsCase


@tagged("post_install", "-at_install")
class TestChannexBookingRatePlan(ChannexRestrictionsCase):
    """A booking sold under one of the rate plans of the pricelist.

    The OTA sells the room and its board service at one price a night, the
    price the rate plan was published at: here 100 for the room with its two
    adults, and the breakfast at 10 a guest, which is consumed the morning
    after the night it goes with.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.breakfast.write({"per_day": True, "consumed_on": "after"})

    # -- helpers -------------------------------------------------------------

    def _sold(self, rate_plan, prices=("120.00", "120.00"), **values):
        """A room of two adults for the nights of the 13th and 14th."""
        return self._room(
            **{
                "rate_plan_id": rate_plan.external_id if rate_plan else None,
                "days": dict(zip(("2026-11-13", "2026-11-14"), prices, strict=True)),
                "occupancy": {"adults": 2, "children": 0, "infants": 0},
                **values,
            }
        )

    def _book(self, room, **values):
        self._seed_booking(rooms=[room], **values)
        self._import()
        return self._folios().odoo_id.reservation_ids

    def _room_prices(self, reservation):
        return reservation.reservation_line_ids.sorted("date").mapped("price")

    def _board_lines(self, reservation):
        return (
            reservation.service_ids.filtered("is_board_service")
            .service_line_ids.sorted("date")
            .mapped(lambda line: (str(line.date), line.day_qty, line.price_unit))
        )

    def _charged(self, reservation):
        """What the stay comes to: its nights, and its board service."""
        board = reservation.service_ids.filtered("is_board_service").service_line_ids
        return sum(reservation.reservation_line_ids.mapped("price")) + sum(
            line.price_unit * line.day_qty for line in board
        )

    # -- what the room is sold under ---------------------------------------

    def test_the_board_service_sold_is_the_one_served(self):
        reservation = self._book(self._sold(self.breakfast_plan))
        self.assertEqual(reservation.pricelist_id, self.pricelist)
        self.assertEqual(reservation.board_service_id, self.breakfast)

    def test_room_only_is_no_board_service_whatever_the_pricelist_defaults_to(self):
        """Left to pms, the reservation gets the default board service of its
        pricelist, charged on top of a price that does not include it."""
        self.pricelist.default_board_service_id = self.breakfast
        reservation = self._book(self._sold(self.room_only_plan, ("100.00", "100.00")))
        self.assertFalse(reservation.board_service_id)
        self.assertFalse(reservation.service_ids)
        self.assertEqual(self._room_prices(reservation), [100.0, 100.0])

    def test_the_pricelist_is_the_rate_plan_s_and_not_the_agency_s(self):
        """An agency can make pms price everything it sells with a pricelist
        of its own. What the OTA sold under is what says how calling the stay
        off is charged."""
        sold_under = self.env["product.pricelist"].create(
            {"name": "Channex PL 2", "company_id": self.company.id}
        )
        rate_plan = self.env["channel.channex.rate.plan"].create(
            {
                "backend_id": self.backend.id,
                "pricelist_id": sold_under.id,
                "board_service_id": self.room_only.id,
                "room_type_binding_id": self.room_type_binding.id,
                "external_id": "rp-2",
            }
        )
        agency = (
            self.env["res.partner"]
            .with_company(self.company)
            .create(
                {
                    "name": "An agency",
                    "is_agency": True,
                    "apply_pricelist": True,
                    "sale_channel_id": self.env["pms.sale.channel"]
                    .create({"name": "An OTA", "channel_type": "indirect"})
                    .id,
                    "property_product_pricelist": self.pricelist.id,
                }
            )
        )
        self.env["channel.channex.channel"].create(
            {
                "backend_id": self.backend.id,
                "external_id": "chn-1",
                "code": "BookingCom",
                "title": "BookingCom - Channex Property",
                "ota_id": self.env["channel.channex.ota"]
                ._channex_of_code("BookingCom")
                .id,
            }
        ).agency_id = agency
        reservation = self._book(self._sold(rate_plan))
        self.assertEqual(reservation.agency_id, agency)
        self.assertEqual(reservation.pricelist_id, sold_under)

    def test_an_occupancy_option_is_its_rate_plan(self):
        """Channex books an occupancy other than the primary one under an id of
        its own, and names the rate plan it belongs to in the meta."""
        reservation = self._book(
            self._sold(
                self.breakfast_plan,
                rate_plan_id="option-of-one-adult",
                meta={"parent_rate_plan_id": self.breakfast_plan.external_id},
            )
        )
        self.assertEqual(self._revision().state, "applied")
        self.assertEqual(reservation.board_service_id, self.breakfast)

    def test_a_rate_plan_being_taken_down_still_lands_what_it_sold(self):
        self.breakfast_plan.channex_to_remove = True
        reservation = self._book(self._sold(self.breakfast_plan))
        self.assertEqual(reservation.board_service_id, self.breakfast)

    # -- what each part costs ----------------------------------------------

    def test_the_board_service_at_what_was_published_and_the_room_the_rest(self):
        """An OTA discount falls on the room: the breakfast is what it is."""
        reservation = self._book(self._sold(self.breakfast_plan, ("108.00", "108.00")))
        self.assertEqual(self._room_prices(reservation), [88.0, 88.0])
        self.assertEqual(
            self._board_lines(reservation),
            [("2026-11-14", 2, 10.0), ("2026-11-15", 2, 10.0)],
        )
        self.assertEqual(self._charged(reservation), 216.0)

    def test_a_breakfast_is_priced_on_the_night_it_goes_with(self):
        """Served the morning after, and published with the night before."""
        night = datetime.date(2026, 11, 13)
        self._item(
            self.breakfast,
            15,
            date_start_consumption=night,
            date_end_consumption=night,
        )
        reservation = self._book(self._sold(self.breakfast_plan, ("130.00", "120.00")))
        self.assertEqual(self._room_prices(reservation), [100.0, 100.0])
        self.assertEqual(
            self._board_lines(reservation),
            [("2026-11-14", 2, 15.0), ("2026-11-15", 2, 10.0)],
        )

    def test_with_a_child_the_stay_still_comes_to_what_the_ota_sold(self):
        reservation = self._book(
            self._sold(
                self.breakfast_plan,
                ("150.00", "150.00"),
                occupancy={"adults": 2, "children": 1, "infants": 0},
            )
        )
        self.assertEqual(self._charged(reservation), 300.0)

    # -- what cannot be applied ---------------------------------------------

    def test_a_rate_plan_that_is_not_ours_is_not_applied(self):
        """Made by hand on Channex, or taken down since: either way nothing
        says what was sold with the room."""
        self._seed_booking(rooms=[self._sold(None, rate_plan_id="made-by-hand")])
        self._import()
        self.assertFalse(self._folios())
        self.assertEqual(self._revision().state, "error")
        self.assertIn("made-by-hand", self._revision().error)

    def test_a_room_sold_under_no_rate_plan_is_not_applied(self):
        """What Channex sends for a room the channel has not mapped."""
        self._seed_booking(rooms=[self._sold(None)])
        self._import()
        self.assertFalse(self._folios())
        self.assertEqual(self._revision().state, "error")

    def test_a_night_that_does_not_pay_for_its_board_service_is_not_applied(self):
        self._seed_booking(rooms=[self._sold(self.breakfast_plan, ("120.00", "20.00"))])
        self._import()
        self.assertFalse(self._folios())
        self.assertIn("2026-11-14", self._revision().error)
        self.assertNotIn("2026-11-13", self._revision().error)

    # -- a booking that changes -----------------------------------------------

    def test_another_rate_plan_is_the_same_stay_sold_differently(self):
        booked = self._book(self._sold(self.room_only_plan, ("100.00", "100.00")))
        self._seed_booking(
            external_id="r2",
            status="modified",
            inserted_at="2026-11-12T18:20:00.000000",
            rooms=[self._sold(self.breakfast_plan, ("115.00", "115.00"))],
        )
        self._import()
        self.assertEqual(self._revision("r2").state, "applied")
        reservation = self._folios().odoo_id.reservation_ids
        self.assertEqual(reservation, booked)
        self.assertEqual(reservation.board_service_id, self.breakfast)
        self.assertEqual(self._room_prices(reservation), [95.0, 95.0])
        self.assertEqual(
            self._board_lines(reservation),
            [("2026-11-14", 2, 10.0), ("2026-11-15", 2, 10.0)],
        )

    def test_a_cancellation_does_not_ask_for_the_rate_plan(self):
        """Nothing holds up a cancellation, least of all what it was sold
        under: the booking already says."""
        reservation = self._book(self._sold(self.breakfast_plan))
        self._seed_booking(
            external_id="r2",
            status="cancelled",
            inserted_at="2026-11-12T18:20:00.000000",
            rooms=[self._sold(None, rate_plan_id="gone")],
        )
        self._import()
        self.assertEqual(self._revision("r2").state, "applied")
        self.assertEqual(reservation.state, "cancel")
        self.assertEqual(reservation.board_service_id, self.breakfast)
