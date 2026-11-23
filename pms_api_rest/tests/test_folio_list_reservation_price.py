# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import datetime

from odoo.tests import tagged

from odoo.addons.base_rest.controllers.main import _PseudoCollection
from odoo.addons.component.core import WorkContext
from odoo.addons.pms.tests.common import TestPms


@tagged("post_install", "-at_install")
class TestFolioListReservationPrice(TestPms):
    """The folio list and the folio detail draw each reservation with the same
    card, so both must send the same priceTotal: room plus services."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(user=cls.env["res.users"].browse(1))
        cls.env.user.pms_property_ids = [(4, cls.pms_property1.id)]
        room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.pms_property1.id],
                "name": "Folio list price",
                "default_code": "FLP",
                "class_id": cls.room_type_class1.id,
                "list_price": 50,
            }
        )
        for name in ("Room F1", "Room F2"):
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
        product = cls.env["product.product"].create(
            {"name": "Parking folio list", "list_price": 12}
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
        cls.with_services = cls.env["pms.reservation"].create(dict(vals))
        cls.env["pms.service"].create(
            {"reservation_id": cls.with_services.id, "product_id": product.id}
        )
        cls.without_services = cls.env["pms.reservation"].create(
            dict(vals, folio_id=cls.with_services.folio_id.id)
        )

    def _service(self):
        collection = _PseudoCollection("pms.services", self.env)
        work = WorkContext(
            model_name="rest.service.registration", collection=collection
        )
        return work.component(usage="folios")

    def _list_prices(self):
        param = self.env.datamodels["pms.folio.search.param"](
            pmsPropertyId=self.pms_property1.id,
            ids=[self.with_services.folio_id.id],
        )
        (folio,) = self._service().get_folios(param)
        return {r["id"]: r["priceTotal"] for r in folio.reservations}

    def test_list_price_includes_services(self):
        self.assertTrue(self.with_services.price_services)
        self.assertEqual(
            self._list_prices()[self.with_services.id],
            round(
                self.with_services.price_total + self.with_services.price_services,
                2,
            ),
        )

    def test_list_price_matches_folio_detail(self):
        detail = {
            r.id: r.priceTotal
            for r in self._service().get_folio_reservations(
                self.with_services.folio_id.id
            )
        }
        self.assertEqual(self._list_prices(), detail)

    def test_list_price_without_services_is_room_price(self):
        self.assertEqual(
            self._list_prices()[self.without_services.id],
            round(self.without_services.price_total, 2),
        )
