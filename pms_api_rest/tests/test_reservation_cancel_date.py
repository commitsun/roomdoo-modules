# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import datetime

from odoo.tests import tagged

from odoo.addons.base_rest.controllers.main import _PseudoCollection
from odoo.addons.component.core import WorkContext
from odoo.addons.pms.tests.common import TestPms


@tagged("post_install", "-at_install")
class TestReservationCancelDate(TestPms):
    """Pick-up reports need to know when each reservation was cancelled, and to
    fetch the cancellations of a window without walking every reservation."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(user=cls.env["res.users"].browse(1))
        cls.env.user.pms_property_ids = [(4, cls.pms_property1.id)]
        cls.pms_property1.tz = "Europe/Madrid"
        room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.pms_property1.id],
                "name": "Cancel date",
                "default_code": "CXD",
                "class_id": cls.room_type_class1.id,
            }
        )
        for name in ("Room C1", "Room C2"):
            cls.env["pms.room"].create(
                {
                    "pms_property_id": cls.pms_property1.id,
                    "name": name,
                    "room_type_id": room_type.id,
                    "capacity": 2,
                }
            )
        partner = cls.env["res.partner"].create({"name": "Booker"})
        channel = cls.env["pms.sale.channel"].create(
            {"name": "Door", "channel_type": "direct"}
        )
        checkin = datetime.date.today() + datetime.timedelta(days=10)
        vals = {
            "pms_property_id": cls.pms_property1.id,
            "checkin": checkin,
            "checkout": checkin + datetime.timedelta(days=2),
            "adults": 1,
            "room_type_id": room_type.id,
            "partner_id": partner.id,
            "sale_channel_origin_id": channel.id,
        }
        cls.cancelled = cls.env["pms.reservation"].create(dict(vals))
        cls.reactivated = cls.env["pms.reservation"].create(dict(vals))
        (cls.cancelled | cls.reactivated).action_cancel()
        cls.reactivated.action_confirm()
        # Stored in UTC: late on the 14th is already the 15th in Madrid
        cls.cancel_utc = datetime.datetime(2026, 10, 14, 23, 30)
        (cls.cancelled | cls.reactivated).cancel_datetime = cls.cancel_utc
        cls.cancelled.cancelled_reason = "intime"

    def _service(self):
        collection = _PseudoCollection("pms.services", self.env)
        work = WorkContext(
            model_name="rest.service.registration", collection=collection
        )
        return work.component(usage="reservations")

    def _search(self, **params):
        param = self.env.datamodels["pms.reservation.search.param"](
            pmsPropertyId=self.pms_property1.id, **params
        )
        return {info.id: info for info in self._service().get_reservations(param)}

    def test_list_returns_cancel_date_in_property_time(self):
        info = self._search(ids=[self.cancelled.id])[self.cancelled.id]
        self.assertEqual(info.cancelDate, "2026-10-15T01:30:00+02:00")
        self.assertEqual(info.cancelledReason, "intime")

    def test_detail_returns_cancel_date(self):
        param = self.env.datamodels["pms.search.param"](
            pmsPropertyId=self.pms_property1.id
        )
        info = self._service().get_reservation(self.cancelled.id, param)
        self.assertEqual(info.cancelDate, "2026-10-15T01:30:00+02:00")

    def test_reactivated_reservation_has_no_cancel_date(self):
        """The stored date outlives a reactivation; the API must not show it."""
        info = self._search(ids=[self.reactivated.id])[self.reactivated.id]
        self.assertIsNone(info.cancelDate)

    def test_filter_by_cancel_date_window(self):
        found = self._search(
            cancelDateFrom="2026-10-14 23:00:00", cancelDateTo="2026-10-15 00:00:00"
        )
        self.assertIn(self.cancelled.id, found)
        self.assertNotIn(self.reactivated.id, found)

    def test_filter_by_cancel_date_outside_window(self):
        found = self._search(cancelDateFrom="2026-10-15 00:00:00")
        self.assertNotIn(self.cancelled.id, found)
