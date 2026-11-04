from datetime import date, timedelta

from freezegun import freeze_time

from odoo.tests.common import new_test_user

from odoo.addons.pms_ai_tool.tools import PARAM_ENVIRONMENT

# The tools run "today" on the Monday of the fixtures. The fixtures are built
# earlier, so that no date of the stays is in the past while creating them.
TODAY = date(2026, 9, 21)
SETUP_DAY = "2026-09-01"
CALL_TIME = f"{TODAY} 10:00:00"

PMS_USER_GROUPS = "base.group_user,pms.group_pms_user"


class OperationsToolsMixin:
    """Two hotels, their rooms, two users and a reservation builder."""

    @classmethod
    def _setup_hotels(cls, rooms=5, other_rooms=2):
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        plan = cls.env["pms.availability.plan"].create({"name": "AI ops plan"})
        pricelist = cls.env["product.pricelist"].create(
            {
                "name": "AI ops pricelist",
                "availability_plan_id": plan.id,
                "is_pms_available": True,
            }
        )
        cls.prop, cls.other_prop = cls.env["pms.property"].create(
            [
                {
                    "name": name,
                    "company_id": cls.env.company.id,
                    "default_pricelist_id": pricelist.id,
                }
                for name in ("AI ops hotel", "AI ops other hotel")
            ]
        )
        both = cls.prop | cls.other_prop
        sleeping = cls.env["pms.room.type.class"].create(
            {"name": "AI ops class", "default_code": "AIO"}
        )
        meeting = cls.env["pms.room.type.class"].create(
            {"name": "AI ops meeting class", "default_code": "AIM", "overnight": False}
        )
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [(6, 0, both.ids)],
                "name": "AI ops double",
                "default_code": "AIOD",
                "class_id": sleeping.id,
            }
        )
        cls.meeting_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [(6, 0, cls.prop.ids)],
                "name": "AI ops hall",
                "default_code": "AIOH",
                "class_id": meeting.id,
            }
        )
        rooms_vals = [
            {
                "pms_property_id": prop.id,
                "name": f"Room {n}",
                "short_name": f"O{n}",
                "room_type_id": cls.room_type.id,
                "capacity": 2,
            }
            for prop, count in ((cls.prop, rooms), (cls.other_prop, other_rooms))
            for n in range(count)
        ]
        rooms_vals.append(
            {
                "pms_property_id": cls.prop.id,
                "name": "Hall",
                "short_name": "HALL",
                "room_type_id": cls.meeting_type.id,
                "capacity": 20,
            }
        )
        cls.env["pms.room"].create(rooms_vals)
        cls.channel = cls.env["pms.sale.channel"].create(
            {"name": "AI ops door", "channel_type": "direct"}
        )
        cls.partner = cls.env["res.partner"].create({"name": "AI ops guest"})
        cls.closure = cls.env["room.closure.reason"].create({"name": "AI ops repairs"})
        cls.user = new_test_user(
            cls.env,
            login="ai_ops",
            groups=PMS_USER_GROUPS,
            pms_property_ids=[(6, 0, cls.prop.ids)],
            pms_property_id=cls.prop.id,
        )
        cls.user_all = new_test_user(
            cls.env,
            login="ai_ops_all",
            groups=PMS_USER_GROUPS,
            pms_property_ids=[(6, 0, both.ids)],
            pms_property_id=cls.prop.id,
        )
        params = cls.env["ir.config_parameter"].sudo()
        params.set_param(PARAM_ENVIRONMENT, "test")

    @classmethod
    def day(cls, offset):
        """The day ``offset`` days after TODAY."""
        return TODAY + timedelta(days=offset)

    @classmethod
    def _reservation(
        cls,
        checkin,
        checkout,
        state="confirm",
        prop=None,
        rtype="normal",
        adults=2,
        children=0,
        room_type=None,
        partner=None,
        guests_in=1,
        forced=False,
        **extra,
    ):
        """Create a reservation from ``checkin`` to ``checkout`` (day offsets).

        ``state`` can be draft, confirm, cancel, arrival_delayed, onboard,
        departure_delayed or done. Guests on board are ``guests_in`` of them.
        """
        vals = {
            "pms_property_id": (prop or cls.prop).id,
            "room_type_id": (room_type or cls.room_type).id,
            "partner_id": (partner or cls.partner).id,
            "sale_channel_origin_id": cls.channel.id,
            "checkin": cls.day(checkin),
            "checkout": cls.day(checkout),
            "reservation_type": rtype,
            "preconfirm": state != "draft",
            **extra,
        }
        if rtype == "out":
            vals["closure_reason_id"] = cls.closure.id
        elif adults is not None:
            vals.update(adults=adults, children=children)
        with freeze_time(SETUP_DAY):
            reservation = (
                cls.env["pms.reservation"]
                .with_context(force_overbooking=forced)
                .create(vals)
            )
            guests = reservation.checkin_partner_ids.sorted("id")
            if state == "cancel":
                reservation.action_cancel()
            elif state in ("onboard", "departure_delayed"):
                guests[:guests_in].write({"state": "onboard"})
                reservation.write({"state": state})
            elif state == "done":
                guests.write({"state": "done"})
                reservation.write({"state": "done"})
            elif state == "arrival_delayed":
                reservation.write({"state": state})
        return reservation

    def _call(self, tool, user=None, _now=CALL_TIME, **kwargs):
        """Run a tool as ``user`` (the hotel user) at ``_now``."""
        record = self.env.ref(f"pms_ai_tool_operations.ai_tool_{tool}")
        with freeze_time(_now):
            return record.with_user(user or self.user)._execute_tool(**kwargs)
