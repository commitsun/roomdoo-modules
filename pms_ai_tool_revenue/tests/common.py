from datetime import date, timedelta

from freezegun import freeze_time

from odoo.tests.common import new_test_user

from odoo.addons.pms_ai_tool.tools import PARAM_ENVIRONMENT


class RevenueFixture:
    """Three hotels with rooms, channels, VAT and reservations in two periods.

    Current period: 2026-09-21 to 2026-09-27. Last year: 2025-09-21 to
    2025-09-27. The user sees the first two hotels only. The nights of the
    current period, for the first hotel (rooms: 4 + a meeting room):

    * A, direct: 21-23, 110 each (VAT 10% included), no discount
    * B, OTA: 24 at 220 and 25 at 220 with 50% off
    * C, direct: 26-27, worth 0 (a courtesy)
    * Ag, through an agency: 24-25, 110 each
    * D, staff, night 21; E, out of service, nights 22-23
    * F, cancelled, night 21; G, in the meeting room, night 21 (not overnight)

    Second hotel: P2A, direct, 22-23 at 220. Third hotel (out of scope): P3.
    """

    @classmethod
    def _setup_revenue(cls):
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.env["ir.config_parameter"].sudo().set_param(PARAM_ENVIRONMENT, "test")
        plan = cls.env["pms.availability.plan"].create({"name": "AI plan"})
        pricelist = cls.env["product.pricelist"].create(
            {
                "name": "AI pricelist",
                "availability_plan_id": plan.id,
                "is_pms_available": True,
            }
        )
        # As the pms tests do: every pricelist usable by the PMS (agencies get
        # the default one).
        for other in cls.env["product.pricelist"].search([]):
            if not other.availability_plan_id:
                other.write({"availability_plan_id": plan.id, "is_pms_available": True})
        cls.prop1, cls.prop2, cls.prop3 = cls.env["pms.property"].create(
            [
                {
                    "name": f"AI revenue property {n}",
                    "company_id": cls.env.company.id,
                    "default_pricelist_id": pricelist.id,
                }
                for n in (1, 2, 3)
            ]
        )
        props = cls.prop1 | cls.prop2 | cls.prop3
        classes = cls.env["pms.room.type.class"].create(
            [
                {"name": "AI class", "default_code": "AIC"},
                {"name": "AI meeting", "default_code": "AIM", "overnight": False},
            ]
        )
        tax = cls.env["account.tax"].create(
            {
                "name": "AI VAT 10",
                "amount": 10,
                "amount_type": "percent",
                "type_tax_use": "sale",
                "price_include": True,
                "company_id": cls.env.company.id,
            }
        )
        cls.room_type, cls.meeting_type = cls.env["pms.room.type"].create(
            [
                {
                    "pms_property_ids": [(6, 0, props.ids)],
                    "name": name,
                    "default_code": code,
                    "class_id": room_class.id,
                }
                for name, code, room_class in (
                    ("AI double", "AID", classes[0]),
                    ("AI meeting room", "AIMR", classes[1]),
                )
            ]
        )
        for room_type in cls.room_type | cls.meeting_type:
            room_type.product_id.taxes_id = [(6, 0, tax.ids)]
        for prop, count in ((cls.prop1, 4), (cls.prop2, 2), (cls.prop3, 2)):
            for n in range(count):
                cls._room(prop, f"Room {n}", f"R{n}")
        cls.old_room = cls._room(cls.prop1, "Old room", "OLD")
        cls.new_room = cls._room(cls.prop2, "New room", "NEW")
        cls._room(cls.prop1, "Meeting", "MEE", cls.meeting_type)
        cls.closure_reason = cls.env["room.closure.reason"].create(
            {"name": "AI closed"}
        )
        cls.direct = cls.env["pms.sale.channel"].create(
            {"name": "AI door", "channel_type": "direct"}
        )
        cls.ota = cls.env["pms.sale.channel"].create(
            {"name": "AI OTA", "channel_type": "indirect"}
        )
        cls.agency = cls.env["res.partner"].create(
            {
                "name": "AI Agency",
                "is_company": True,
                "is_agency": True,
                "sale_channel_id": cls.ota.id,
            }
        )
        cls.guest = cls.env["res.partner"].create({"name": "AI guest"})
        cls.user = new_test_user(
            cls.env,
            login="ai_revenue",
            groups="base.group_user,pms.group_pms_user",
            pms_property_ids=[(6, 0, (cls.prop1 | cls.prop2).ids)],
            pms_property_id=cls.prop1.id,
            tz="Europe/Madrid",
        )
        cls._setup_last_year()
        cls._setup_current_year()
        cls._finish_revenue_fixture()

    @classmethod
    def _room(cls, prop, name, short_name, room_type=None):
        return cls.env["pms.room"].create(
            {
                "pms_property_id": prop.id,
                "name": name,
                "short_name": short_name,
                "room_type_id": (room_type or cls.room_type).id,
                "capacity": 2,
            }
        )

    @classmethod
    def _reservation(
        cls,
        prop,
        channel,
        nights,
        kind="normal",
        agency=None,
        room=None,
        room_type=None,
        created=None,
    ):
        """Create a reservation with (date, price, discount) nights."""
        vals = {
            "pms_property_id": prop.id,
            "room_type_id": (room_type or cls.room_type).id,
            "partner_id": cls.guest.id,
            "sale_channel_origin_id": channel.id,
            "reservation_type": kind,
            "reservation_line_ids": [(0, 0, {"date": day}) for day, _p, _d in nights],
        }
        if kind == "out":
            vals["closure_reason_id"] = cls.closure_reason.id
        if agency:
            vals["agency_id"] = agency.id
        if room:
            vals["preferred_room_id"] = room.id
        reservation = cls.env["pms.reservation"].create(vals)
        for line, (_day, price, discount) in zip(
            reservation.reservation_line_ids.sorted("date"), nights, strict=True
        ):
            line.write(
                {"price": price, "discount": discount, "sale_channel_id": channel.id}
            )
        if created:
            cls._stamp(reservation, created)
        return reservation

    @classmethod
    def _stamp(cls, reservation, created=None, cancelled=None, modified=False):
        """Set the creation moment (UTC) and cancel the reservation at a moment."""
        if created:
            cls.env.flush_all()
            cls.env.cr.execute(
                "UPDATE pms_reservation SET create_date = %s WHERE id = %s",
                [created, reservation.id],
            )
            cls.env.invalidate_all()
        if cancelled:
            with freeze_time(cancelled):
                reservation.with_context(modified=modified).action_cancel()

    @classmethod
    def _nights(cls, year, first_day, prices):
        """Nights of September from ``first_day`` with (price, discount)."""
        return [
            (date(year, 9, first_day) + timedelta(days=offset), price, discount)
            for offset, (price, discount) in enumerate(prices)
        ]

    @classmethod
    def _setup_last_year(cls):
        with freeze_time("2025-08-01"):
            cls.ly_a = cls._reservation(
                cls.prop1,
                cls.direct,
                cls._nights(2025, 22, [(99, 0), (99, 0)]),
                room=cls.old_room,
                created="2025-08-01 08:00:00",
            )
            cls.ly_b = cls._reservation(
                cls.prop1,
                cls.direct,
                cls._nights(2025, 25, [(121, 0)]),
                created="2025-09-25 08:00:00",
            )
            # Cancelled before, and after, the cut-off of the pace tool, and
            # cancelled without a date.
            cls.ly_early = cls._reservation(
                cls.prop1,
                cls.direct,
                cls._nights(2025, 24, [(110, 0)]),
                created="2025-08-10 08:00:00",
            )
            cls.ly_late = cls._reservation(
                cls.prop1,
                cls.direct,
                cls._nights(2025, 26, [(110, 0)]),
                created="2025-08-10 08:00:00",
            )
            cls.ly_undated = cls._reservation(
                cls.prop1,
                cls.direct,
                cls._nights(2025, 27, [(110, 0)]),
                created="2025-08-10 08:00:00",
            )
        cls._stamp(cls.ly_early, cancelled="2025-09-10 09:00:00")
        cls._stamp(cls.ly_late, cancelled="2025-09-22 09:00:00")
        cls._stamp(cls.ly_undated, cancelled="2025-09-11 09:00:00")
        cls.old_room.active = False

    @classmethod
    def _setup_current_year(cls):
        night = cls._nights
        with freeze_time("2026-09-01"):
            cls.res_a = cls._reservation(
                cls.prop1,
                cls.direct,
                night(2026, 21, [(110, 0)] * 3),
                created="2026-09-15 10:00:00",
            )
            cls.res_b = cls._reservation(
                cls.prop1,
                cls.ota,
                night(2026, 24, [(220, 0), (220, 50)]),
                created="2026-09-10 10:00:00",
            )
            # Created just after midnight in Madrid: outside the booking window
            # in Madrid time, inside it in UTC.
            cls.res_c = cls._reservation(
                cls.prop1,
                cls.direct,
                night(2026, 26, [(0, 0), (0, 0)]),
                created="2026-09-20 22:30:00",
            )
            # The opposite: just after midnight in Madrid at the start of the
            # window, so inside it in Madrid time and outside it in UTC.
            cls.res_ag = cls._reservation(
                cls.prop1,
                cls.ota,
                night(2026, 24, [(110, 0), (110, 0)]),
                agency=cls.agency,
                created="2026-09-13 22:30:00",
            )
            cls.res_staff = cls._reservation(
                cls.prop1,
                cls.direct,
                night(2026, 21, [(50, 0)]),
                kind="staff",
                created="2026-09-16 10:00:00",
            )
            cls.res_out = cls._reservation(
                cls.prop1,
                cls.direct,
                night(2026, 22, [(0, 0), (0, 0)]),
                kind="out",
                created="2026-09-16 10:00:00",
            )
            cls.res_cancelled = cls._reservation(
                cls.prop1,
                cls.direct,
                night(2026, 21, [(110, 0)]),
                created="2026-09-16 10:00:00",
            )
            cls.res_meeting = cls._reservation(
                cls.prop1,
                cls.direct,
                night(2026, 21, [(500, 0)]),
                room_type=cls.meeting_type,
                created="2026-09-16 10:00:00",
            )
            # Booked before the window and cancelled in it, and cancelled before.
            cls.res_before = cls._reservation(
                cls.prop1,
                cls.direct,
                night(2026, 24, [(110, 0), (110, 0)]),
                created="2026-09-01 10:00:00",
            )
            cls.res_old_cancel = cls._reservation(
                cls.prop1,
                cls.direct,
                night(2026, 27, [(110, 0)]),
                created="2026-09-01 10:00:00",
            )
            # Created in the window and replaced by a modification.
            cls.res_modified = cls._reservation(
                cls.prop1,
                cls.direct,
                night(2026, 22, [(110, 0)]),
                created="2026-09-16 10:00:00",
            )
            cls.res_undated = cls._reservation(
                cls.prop1,
                cls.direct,
                night(2026, 26, [(110, 0)]),
                created="2026-09-02 10:00:00",
            )
            cls.res_p2 = cls._reservation(
                cls.prop2,
                cls.direct,
                night(2026, 22, [(220, 0), (220, 0)]),
                created="2026-09-17 10:00:00",
            )
            cls.res_p3 = cls._reservation(
                cls.prop3,
                cls.direct,
                night(2026, 22, [(300, 0)]),
                created="2026-09-17 10:00:00",
            )
        cls._stamp(cls.res_cancelled, cancelled="2026-09-18 09:00:00")
        cls._stamp(cls.res_before, cancelled="2026-09-17 09:00:00")
        cls._stamp(cls.res_old_cancel, cancelled="2026-09-05 09:00:00")
        cls._stamp(cls.res_modified, cancelled="2026-09-19 09:00:00", modified=True)
        cls._stamp(cls.res_undated, cancelled="2026-09-03 09:00:00")

    @classmethod
    def _finish_revenue_fixture(cls):
        cls.env.flush_all()
        cr = cls.env.cr
        # The PMS reprices nights stored at 0 whenever it recomputes them, so
        # set the courtesy as it is found in real data.
        cr.execute(
            """UPDATE pms_reservation_line SET price = 0, price_day_total = 0
                WHERE reservation_id = %s""",
            [cls.res_c.id],
        )
        # A cancelled reservation that kept no cancel date, changed later.
        cr.execute(
            """UPDATE pms_reservation
                  SET cancel_datetime = NULL, write_date = '2026-09-18 12:00:00'
                WHERE id = %s""",
            [cls.res_undated.id],
        )
        cr.execute(
            "UPDATE pms_reservation SET cancel_datetime = NULL WHERE id = %s",
            [cls.ly_undated.id],
        )
        # Rooms: all existed long ago but the new one; the old one is archived.
        cr.execute("UPDATE pms_room SET create_date = '2024-01-01 08:00:00'")
        cr.execute(
            "UPDATE pms_room SET create_date = '2026-09-25 08:00:00' WHERE id = %s",
            [cls.new_room.id],
        )
        cls.env.invalidate_all()

    def _tool(self, name):
        return self.env.ref(f"pms_ai_tool_revenue.ai_tool_{name}")

    def _run(self, name, **kwargs):
        return self._tool(name).with_user(self.user)._execute_tool(**kwargs)

    def _by_property(self, response, key="by_property"):
        return {row["property_id"]: row for row in response["result"][key]}
