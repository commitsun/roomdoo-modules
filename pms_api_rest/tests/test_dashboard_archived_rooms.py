# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""The dashboard counts only the rooms a property can sell.

Archived rooms used to count as free rooms and lowered the occupancy rate,
so a property that archived half of its rooms looked half empty when full.
"""
import datetime

from odoo.tests import tagged

from odoo.addons.base_rest.controllers.main import _PseudoCollection
from odoo.addons.component.core import WorkContext
from odoo.addons.pms.tests.common import TestPms


@tagged("post_install", "-at_install")
class TestDashboardArchivedRooms(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Admin (uid=1) avoids record rules; the endpoints sudo() anyway.
        cls.env = cls.env(user=cls.env["res.users"].browse(1))
        cls.env.user.write(
            {
                "company_ids": [(4, cls.pms_property1.company_id.id)],
                "pms_property_ids": [(4, cls.pms_property1.id)],
            }
        )
        room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.pms_property1.id],
                "name": "Double Dashboard",
                "default_code": "DBL_DSH",
                "class_id": cls.room_type_class1.id,
            }
        )
        rooms = cls.env["pms.room"].create(
            [
                {
                    "pms_property_id": cls.pms_property1.id,
                    "name": "Room %s" % number,
                    "short_name": "D%s" % number,
                    "room_type_id": room_type.id,
                    "capacity": 2,
                }
                for number in (401, 402, 403)
            ]
        )
        rooms[2].active = False
        cls.date = datetime.date.today() + datetime.timedelta(days=30)
        cls.env["pms.reservation"].create(
            {
                "checkin": cls.date,
                "checkout": cls.date + datetime.timedelta(days=1),
                "room_type_id": room_type.id,
                "preferred_room_id": rooms[0].id,
                "partner_id": cls.env["res.partner"].create({"name": "Guest"}).id,
                "pms_property_id": cls.pms_property1.id,
                "sale_channel_origin_id": cls.env["pms.sale.channel"]
                .create({"name": "Direct", "channel_type": "direct"})
                .id,
            }
        )

    def _service(self, usage):
        collection = _PseudoCollection("pms.services", self.env)
        work = WorkContext(
            model_name="rest.service.registration", collection=collection
        )
        return work.component(usage=usage)

    def test_state_rooms_ignore_archived_rooms(self):
        param = self.env.datamodels["pms.dashboard.range.dates.search.param"](
            dateFrom=self.date.isoformat(),
            dateTo=self.date.isoformat(),
            pmsPropertyId=self.pms_property1.id,
        )
        (state,) = self._service("dashboard").get_state_rooms(param)
        self.assertEqual(state.numOccupiedRooms, 1)
        self.assertEqual(state.numFreeRooms, 1)

    def test_occupancy_ignores_archived_rooms(self):
        param = self.env.datamodels["pms.dashboard.search.param"](
            date=self.date.isoformat(),
            pmsPropertyId=self.pms_property1.id,
        )
        occupancy = self._service("dashboard").get_occupancy(param)
        self.assertEqual(occupancy.value, 50)
